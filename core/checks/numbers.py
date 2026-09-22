"""Number grammar for the grounding gate.

A regular expression over bare digits is not sufficient: `1,200,000`,
`1.2M` and `1.2 million` all denote the same quantity and must parse to
the same float, and a sentence-final period ("Raised AUC to 0.83.")
must not be misread as the start of a new decimal. Everything downstream
compares parsed values, never the original strings, so this module is
the only place either of those distinctions gets made.

The regex itself does not decide whether a letter run after a number is
a magnitude — it only tokenises "digits, optional gap, optional letters,
optional percent sign". A separate interpretation step in `parse_numbers`
decides whether the letters mean anything, because that decision needs
context a regex alone cannot apply safely: "1.2M" and "1.2 million" are
magnitudes, but "300mph", "900ms", "1.2Mbps" and "2mm" all happen to
start with a magnitude letter while meaning something else entirely. A
negative lookahead after the suffix ("only attach `m` if not followed by
another letter") looks like the fix, but is unsound in a backtracking
engine: when the lookahead fails, the engine does not fail the suffix —
it backtracks the *digits* group instead, so "300mph" would silently
parse as "30" (with the trailing "0" reinterpreted as ordinary text)
rather than cleanly failing to attach a suffix. A silently wrong value
is worse than the collision it would have prevented, since it flows
into a comparison that then passes or fails for the wrong reason.
Tokenising broadly and interpreting the pieces in plain Python avoids
backtracking entirely: the interpretation step can only choose to
extend the match or leave it alone, never reshape what the digits
matched.

The same "reject, don't slide" hazard applies to what precedes a digit
run: an identifier like "p99", "S3" or "H100" embeds a digit run that is
not a quantity at all, and a naive scan happily extracts "99", "3" or
"100" from it. A negative lookbehind that excludes only letters looks
like the fix but has the identical failure mode as the suffix lookahead
above — the engine retries at the next character instead of rejecting
the match, so "p99" would parse as "9" rather than nothing. Excluding
digits and "." from the lookbehind as well closes that gap, because it
then blocks every position inside the run, not just the first one; see
`_TOKEN`.

`values_match` compares two already-parsed values with a relative
tolerance rather than exact equality, because a generated claim is
usually a rounded restatement of a stored figure (`0.8312` reported as
"0.83"), not a copy of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WORD_MAGNITUDES: dict[str, float] = {"thousand": 1e3, "million": 1e6, "billion": 1e9}

# A single-letter abbreviation only attaches when there is no whitespace
# before it ("1.2M", "$12k", "5K"): that spacing is the ordinary written
# convention for an abbreviation, whereas the same bare letter separated by
# a space is far more often a unit ("300 m") than a magnitude someone chose
# to space out. "1.2 M" is therefore deliberately NOT read as a magnitude —
# see test_spaced_single_letter_abbreviation_is_accepted_cost.
#
# "mm" is deliberately absent even though it is a common finance
# abbreviation for "million": that reading is far rarer in this domain than
# "mm" as a millimetre/tolerance unit ("2mm tolerance"), so leaving it out
# means that phrase parses as the literal 2 instead of silently becoming
# two million.
ABBR_MAGNITUDES: dict[str, float] = {"k": 1e3, "m": 1e6, "b": 1e9}

CURRENCY: dict[str, str] = {"$": "USD", "£": "GBP", "€": "EUR"}

# The decimal tail `(?:\.\d+)?` is repeated on both digit branches, not
# shared after the alternation: the grouped-thousands branch needs its own
# tail so `"1,200.50"` parses as one number (1200.5), not as `"1,200"`
# followed by a separate `".50"` read as a second, unrelated number.
#
# The currency-digit separator is `[ ]?` — a single optional space — never
# `\s*`. `\s*` would let a currency symbol attach across a newline or a run
# of tabs to digits that are not actually part of the same written amount;
# a single space is the only separator that plausibly still means "this
# symbol prefixes this number" ("$ 12 fee").
#
# The leading `(?<![A-Za-z0-9.])` rejects a digit run that is part of an
# identifier rather than a standalone quantity — "p99", "S3", "H100",
# "GPT4", "v1.2.3" — instead of matching a truncated substring of one.
# A lookbehind of only `(?<![A-Za-z])` looks like the fix but is unsound
# for the same reason a lookahead was rejected above: when it blocks the
# match at one start position, `finditer` simply retries at the next
# character rather than giving up, so "p99" would parse as "99" and
# "H100" as "00" — a wrong value is silently produced instead of no
# value at all. Including digits (and `.`) in the excluded class closes
# that gap: every position inside a digit run also has a digit or `.`
# immediately before it, so every retry inside the run is blocked too,
# and the whole run is rejected rather than sliding to a shorter match.
# A sign counts only where one can stand -- the start, after whitespace or an
# opening bracket -- and only touching its digits, so a range ("10-15"), a date
# ("2023-2024") and a spaced dash ("Python - 5 years") are not negatives.
_PERCENT_WORDS = frozenset({"percent", "percentage", "pct"})
_TOKEN = re.compile(
    r"(?:(?<![^\s(\[])(?P<sign>[-\u2212])(?=[$£€]?\d)|(?<![A-Za-z0-9.]))"
    r"(?P<currency>[$£€])?[ ]?"
    r"(?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<gap>\s*)"
    r"(?P<alpha>[A-Za-z]+)?"
    r"(?P<percent>\s*%)?"
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

    Handles thousands separators (`1,200,000`), magnitude words and
    abbreviations (`1.2M`, `1.2 million`), currency symbols (`$12k`) and
    percent signs. A letter run immediately after a number is treated as
    a magnitude only when it is exactly a known word (any spacing) or
    exactly a known single-letter abbreviation with no space before it.
    Any other letter run — a unit word ("items"), a unit that happens to
    start with a magnitude letter ("mph", "ms", "Mbps", "mm"), or a
    spaced single-letter abbreviation ("1.2 M") — is left untouched in
    the source text and the number is used as literal digits. This is
    also what keeps a year like `2024` from being read as a magnitude it
    never carries.
    """
    out: list[ParsedNumber] = []
    for match in _TOKEN.finditer(text):
        value = float(match.group("digits").replace(",", ""))
        alpha = match.group("alpha")
        gap = match.group("gap")  # always a string ("" or more) — the group is mandatory
        end = match.end("digits")
        consumed_alpha = False

        if alpha is not None:
            alpha_lower = alpha.lower()
            if alpha_lower in WORD_MAGNITUDES:
                value *= WORD_MAGNITUDES[alpha_lower]
                end = match.end("alpha")
                consumed_alpha = True
            elif gap == "" and len(alpha) == 1 and alpha_lower in ABBR_MAGNITUDES:
                value *= ABBR_MAGNITUDES[alpha_lower]
                end = match.end("alpha")
                consumed_alpha = True
            # Otherwise the letters are not a magnitude: leave them in the
            # source text and do not extend the match past the digits.

        # "15 percent" is a percentage as surely as "15%"; the word lands in
        # the letters group, which is otherwise left in the source text.
        spelled_percent = (
            alpha is not None and not consumed_alpha and alpha.lower() in _PERCENT_WORDS
        )
        if spelled_percent:
            end = match.end("alpha")
            consumed_alpha = True
        percent = match.group("percent")
        # `is_percent` must track the same condition that extends `end`, not
        # merely whether the percent group matched: a match can capture a
        # trailing "%" that was never folded into this number (e.g. an
        # unconsumed unit run directly followed by "%"), and the flag would
        # then describe a character `raw`/`span` don't include.
        consumed_percent = spelled_percent or (
            percent is not None and (alpha is None or consumed_alpha)
        )
        if consumed_percent and percent is not None:
            end = match.end("percent")

        currency = match.group("currency")
        start = match.start("currency") if currency is not None else match.start("digits")
        if match.group("sign") is not None:
            value = -value
            start = match.start("sign")
        out.append(
            ParsedNumber(
                raw=text[start:end],
                value=value,
                unit=CURRENCY.get(currency) if currency else None,
                is_percent=consumed_percent,
                span=(start, end),
            )
        )
    return out


def values_match(claimed: float, source: float, *, tolerance: float = 0.005) -> bool:
    """True when `claimed` is within a relative tolerance of `source`.

    The default (0.5%) admits ordinary rounding — `0.8312` reported as
    "0.83" — and rejects anything that overstates the underlying figure
    by more than that, so "40%" against a stored 44% still fails.
    Tolerance is symmetric: it also rejects understatement, not only
    inflation. Not intended for comparing magnitudes near zero: `source`
    exactly `0` falls back to an absolute check, so the two branches
    disagree right at that boundary.
    """
    if source == 0:
        return abs(claimed) <= tolerance
    return abs(claimed - source) / abs(source) <= tolerance
