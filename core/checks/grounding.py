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

Entity checking has two tiers. A known technology (matched against the
alias table case-insensitively) or an "identifier-shaped" token -- one that
contains a digit, `+`, `#` or `.`, or is 2-6 letters all in capitals --
must appear among the cited facts' entities, through the alias table. The
facts' text does not back it: a mention is not use ("migrated off AWS"), and
a technology name that is also an English word ("R", "Spark") would be backed
by prose. A token the text does state fails with a message saying to list it
in the fact's entities.

Every other capitalised word that does not begin a sentence -- a company, a
client, an award, a title -- must appear as a whole word somewhere in what
the cited facts state (their text, entities, organization, title or
location). A capital mid-sentence marks a name, and a name is a claim: an
invented partner, an award or a promotion reads exactly like ordinary prose
to a rule that checks only digits and acronyms. Sentence-initial words are
exempt because bullets begin with a capitalised verb, and so are tokens glued
to a preceding digit ("1.2M"), which the number rule owns.

Two rules cover what carries neither a digit nor a capital. A short list of
claim words -- award, patent, promoted, founded, valedictorian, doubled,
hundreds, "led a team" and the like (`_CLAIM_PHRASES`) -- must be stated in a
cited fact. A spelled-out number ("twelve") must match a cited quantity or
appear in a fact's words. What remains unchecked is ordinary verbs and
nouns: "led" and "helped with" are not distinguished.

A hyphenated compound ("S3-backed") is checked one hyphen-split component
at a time when the whole token isn't itself a table entry, because `-` has
to stay part of a token for entries like "scikit-learn" to match as a
whole. Every component that is itself checkable must be cited -- accepting
on any single component's match would let a hyphen hide an uncited
technology sitting next to a cited one ("S3-H100" passing on "S3" alone
even though "H100" is not cited, when the space-separated form of the same
claim would correctly fail). A capitalised component ("Redshift") falls to
the name rule like any other capitalised word.

A quantity recorded with a percent unit never backs a plain count, and a
quantity recorded with any other unit never backs a percentage; a quantity
with no unit may back either.

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

from core.aliases import ALIAS_TABLE, canonicalize
from core.checks.derive import DerivationError, compute, date_hull
from core.checks.numbers import parse_numbers, values_match
from core.profile.models import Fact
from core.resume.models import carries_content

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
_PREP_BEFORE = re.compile(
    r"(?<![A-Za-z])(?:from|since|until|through|thru|in|during)\s+$", re.IGNORECASE
)
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
    UNSUPPORTED_NAME = "unsupported_name"
    UNSUPPORTED_CLAIM_WORD = "unsupported_claim_word"
    EMPTY_BULLET = "empty_bullet"
    EMPTY_DOCUMENT = "empty_document"


@dataclass(frozen=True)
class Finding:
    """A single reason a bullet failed to ground, or a note on how it passed.

    `bullet_index` is `-1` for a finding about the document as a whole
    rather than any one bullet -- today only `EMPTY_DOCUMENT`.
    """

    bullet_index: int
    kind: FindingKind
    detail: str


def iso_month_dates(text: str) -> list[str]:
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

    A missing bound is open, as in the month check: `end` is None for an
    ongoing role. With neither bound the facts carry no dates, and a year
    cannot be grounded in them.
    """
    if start is None and end is None:
        return False
    year = int(value)
    return (start is None or int(start[:4]) <= year) and (end is None or year <= int(end[:4]))


def checkable_entity_tokens(text: str) -> list[str]:
    """Every token worth checking against the cited entities — see module docstring."""
    # A trailing full stop ends the sentence, not the token: "Python." is
    # Python, and would otherwise look identifier-shaped for its "." and
    # match no entity. Internal dots ("3.12", "Node.js") are kept.
    tokens = (t.rstrip(".") for t in _TOKEN.findall(text))
    return [
        t
        for t in tokens
        if t and (t.lower() in ALIAS_TABLE or _SHAPED.match(t) or _names_a_technology(t))
    ]


def _names_a_technology(token: str) -> bool:
    # "AWS-hosted" is neither a table entry nor identifier-shaped as a whole,
    # so without this a suffix would hide an uncited technology. Only a
    # component that is itself a table entry of two or more letters counts:
    # the shape rule would also catch "US-based", and the one-letter entry
    # "R" would catch "R-squared", both ordinary English.
    return any(len(p) >= 2 and p.lower() in ALIAS_TABLE for p in token.split("-"))


def _miss_detail(label: str, part: str, corpus: str) -> str:
    # A token the cited fact's own text states is usually a drafting gap in
    # the fact, not an invention in the bullet. It still fails -- a mention
    # is not use ("migrated off AWS") -- but the message names the fix.
    if _has_words(part, corpus):
        return (
            f"{label} is in a cited fact's text but not its entities; add it there "
            "if that fact shows it in use"
        )
    return f"{label} appears in no cited fact"


_PERCENT_UNITS = frozenset({"%", "percent", "pct", "percentage"})


def _is_percent(unit: str | None) -> bool:
    return unit is not None and unit.strip().casefold() in _PERCENT_UNITS


def _is_count(unit: str | None) -> bool:
    """A unit that is present and is not a percent: a percent claim cannot
    rest on it. A quantity with no unit may back either kind of claim."""
    return unit is not None and not _is_percent(unit)


# Words that assert something a reader will take as a specific claim --
# recognition, rank, ownership, scale -- and that the number and entity rules
# cannot see because they carry no digit and no capital. Each must be stated
# in a cited fact. Deliberately a short, readable list: every entry is a word a
# resume uses to inflate, not a word it uses to describe.
_CLAIM_PHRASES = (
    r"awards?",
    r"awarded",
    r"patents?",
    r"patented",
    r"promoted",
    r"promotion",
    r"(?:co-?)?founded",
    r"(?:co-?)?founder",
    r"scholar",
    r"scholarship",
    r"valedictorian",
    r"salutatorian",
    r"summa cum laude",
    r"magna cum laude",
    r"cum laude",
    r"honou?rs",
    r"dean's list",
    r"doubled",
    r"tripled",
    r"quadrupled",
    r"halved",
    r"tenfold",
    r"dozens",
    r"hundreds",
    r"thousands",
    r"millions",
    r"billions",
    r"first",
    r"sole",
    r"best",
    r"head of",
    r"led a team",
    r"managed a team",
    # A relationship with the reader -- "we met", "Alex referred me" -- is a
    # claim about the candidate's history like any other.
    r"met",
    r"suggested",
    r"referred",
    r"referral",
    r"introduced",
    r"recommended",
    r"mutual",
    r"reach(?:ed)? out",
)
_CLAIM_WORDS = re.compile(
    r"(?<![\w'])(?:" + "|".join(_CLAIM_PHRASES) + r")(?![\w'])", re.IGNORECASE
)

_NUMBER_WORDS: dict[str, float] = {
    w: float(n)
    for n, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
} | {
    "thirty": 30.0,
    "forty": 40.0,
    "fifty": 50.0,
    "sixty": 60.0,
    "seventy": 70.0,
    "eighty": 80.0,
    "ninety": 90.0,
    "dozen": 12.0,
    "hundred": 100.0,
    "thousand": 1_000.0,
    "million": 1_000_000.0,
}
# "one" is excluded: "one of the", "one-off" and "no one" are not quantities.
_NUMBER_WORD = re.compile(
    r"(?<![\w-])(" + "|".join(w for w in _NUMBER_WORDS if w != "one") + r")(?![\w-])",
    re.IGNORECASE,
)

_MONTH_AND_DAY_NAMES = frozenset(
    [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
)
# Characters after which a capitalised word may be the first of a sentence, so
# its capital says nothing about it being a name. An opening bracket or quote
# is not one of them: "the (Google) partnership" names Google mid-sentence.
_SENTENCE_BREAK = ".;:!?\u2013\u2014-\u2022"


def _support_corpus(sources: Sequence[Fact]) -> str:
    """Everything the cited facts state in words, normalised for matching."""
    parts: list[str] = []
    for f in sources:
        parts.append(f.text)
        parts.extend(f.entities)
        parts.extend(v for v in (f.organization, f.title, f.location) if v)
    return " ".join(" ".join(parts).casefold().split())


def _has_words(phrase: str, corpus: str) -> bool:
    # The bullet's tokenizer stops at an apostrophe ("Moody's" -> "Moody")
    # while the corpus keeps it, so a lookahead forbidding an apostrophe would
    # reject a name the cited fact states verbatim. A possessive "'s" is
    # therefore allowed to follow the match.
    pattern = (
        r"(?<![\w'])" + re.escape(" ".join(phrase.casefold().split())) + r"(?:['\u2019]s)?(?![\w'])"
    )
    return re.search(pattern, corpus) is not None


def _unsupported_names(text: str, corpus: str, cited_entities: set[str]) -> list[str]:
    """Capitalised words, other than a sentence's first, absent from the cited facts.

    A capital mid-sentence marks a name -- a client, a partner, an award, a
    title -- and a name is a claim. The first word of a sentence is exempt,
    because bullets begin with a capitalised verb. Tokens the entity rule
    already checks (known technologies, identifier-shaped tokens) and month
    and day names (the date rule's) are left to those rules.
    """
    unsupported: list[str] = []
    for match in _TOKEN.finditer(text):
        word = match.group().rstrip(".")
        if not word or not word[0].isupper():
            continue
        if match.start() and text[match.start() - 1].isalnum():
            # Glued to what precedes it: a magnitude ("1.2M") or part of an
            # identifier, which the number and entity rules own.
            continue
        before = text[: match.start()].rstrip()
        if not before or before[-1] in _SENTENCE_BREAK:
            continue
        for part in (p for p in word.split("-") if p and p[0].isupper()):
            if part.casefold() in _MONTH_AND_DAY_NAMES or part in {"I", "A"}:
                continue
            if part.lower() in ALIAS_TABLE or _SHAPED.match(part):
                continue
            if _has_words(part, corpus) or canonicalize(part) in cited_entities:
                continue
            unsupported.append(part)
    return unsupported


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
    if not carries_content(text):
        return Verdict.FAIL, [
            Finding(index, FindingKind.EMPTY_BULLET, "bullet has no letter or digit to ground")
        ]
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

    quantities = [q for f in sources for q in f.numbers.values()]
    percent_values = {q.value for q in quantities if not _is_count(q.unit)}
    count_values = {q.value for q in quantities if not _is_percent(q.unit)}

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
        # A quantity recorded as a percent never backs a plain count, so
        # "42 direct reports" cannot rest on a fact that says 42%.
        # A percentage's direction is carried by its verb ("cut churn by 15%"),
        # so it is compared by magnitude; a count's sign is part of its value.
        if number.is_percent:
            if any(values_match(abs(number.value), abs(v)) for v in percent_values):
                continue
        elif any(values_match(number.value, source) for source in count_values):
            continue
        # A percentage is compared against the *magnitude* of a derived
        # value: the sign of a `pct_change` derivation only reflects the
        # order its two inputs were cited in, not whether the underlying
        # change was an increase or a reduction, so comparing signed values
        # would reject a truthful "increased X 50%" bullet whenever the
        # derivation happened to be phrased as before/after.
        claimed = abs(number.value) if number.is_percent else number.value
        derived_pool = (
            (abs(v) for v in derived_values) if number.is_percent else iter(derived_values)
        )
        if any(values_match(claimed, source) for source in derived_pool):
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
    corpus = _support_corpus(sources)
    for token in checkable_entity_tokens(text):
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
                            _miss_detail(f"{part!r} (component of {token!r})", part, corpus),
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
                _miss_detail(f"{token!r} (canonical form {canonical!r})", token, corpus),
            )
        )

    for word in _unsupported_names(text, corpus, cited_entities):
        findings.append(
            Finding(index, FindingKind.UNSUPPORTED_NAME, f"{word!r} appears in no cited fact")
        )
    for claim in _CLAIM_WORDS.findall(text):
        if not _has_words(claim, corpus):
            findings.append(
                Finding(
                    index,
                    FindingKind.UNSUPPORTED_CLAIM_WORD,
                    f"claim {claim!r} is not stated in any cited fact",
                )
            )
    all_values = {q.value for q in quantities}
    # "1.2 million" is one number, not a number and the word one million:
    # `parse_numbers` folds the word into the value, and its span covers it.
    folded = [n.span for n in parse_numbers(text)]
    for match in _NUMBER_WORD.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in folded):
            continue
        word = match.group(0)
        value = _NUMBER_WORDS[word.casefold()]
        if not _has_words(word, corpus) and not any(values_match(value, v) for v in all_values):
            findings.append(
                Finding(
                    index,
                    FindingKind.UNRESOLVED_NUMBER,
                    f"{word!r} ({value:g}) is not derivable from the cited facts",
                )
            )

    # A year-precision bound covers the whole year: "2024" as an upper bound
    # is 2024-12, not 2024-01, and a plain string comparison makes every month
    # of that year fall outside it.
    low = f"{start}-01" if start is not None and len(start) == 4 else start
    high = f"{end}-12" if end is not None and len(end) == 4 else end
    for iso in iso_month_dates(text):
        if (low is not None and iso < low) or (high is not None and iso > high):
            findings.append(
                Finding(index, FindingKind.UNRESOLVED_DATE, f"{iso} falls outside {low}..{high}")
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

    A document with no bullets fails: every check below is per-bullet, so
    with nothing to iterate none of them fires, and a plain pass would be
    indistinguishable from a document whose every bullet was verified.
    """
    if not bullets:
        return Verdict.FAIL, [
            Finding(-1, FindingKind.EMPTY_DOCUMENT, "document has no bullets to ground")
        ]
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
