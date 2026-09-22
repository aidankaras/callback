"""Skill evidencing: which of a document's listed skills are backed by text
that survived grounding, and which are listed with nothing behind them.

This is deliberately not a gate. It never fails a document and carries no
`Verdict` -- a resume listing a skill it doesn't happen to use in a bullet
is not fabricating anything, it's just terse, and a check that hard-failed
on that would teach a candidate to pad bullets with keywords purely to
satisfy it. What this module reports instead is the honest partition: a
listed skill is either named somewhere the document has already proven
truthful (a bullet that passed grounding), or it isn't, and the caller
decides what to do with that -- surface it as a lint, leave it out of a
report entirely, or something else this module has no opinion on.

`grounded_bullets` is a parameter rather than something computed here on
purpose. "Evidenced" means "named in a bullet that passed grounding," and
grounding needs each bullet's cited facts (`core.checks.grounding`), which
this module has no business knowing about -- it only ever sees bullet
*text*. That keeps `check_skills` a pure function of the document plus a
set of already-grounded strings: testable without constructing a single
`Fact`, and with exactly one place, the caller, deciding what "grounded"
means for a given run.

**Matching a skill against bullet prose is a different operation from
matching a declared skill against a rendered PDF segment**
(`core.checks.roundtrip._find_skill`), and the two must not be confused
despite both starting from the same skill string. A skills run on the page
is *structured*: every item is delimited by a literal comma the renderer
put there, so the round-trip gate can compare an item to a skill exactly
and never worry about one skill's name sitting inside another's ("SQL"
inside "PostgreSQL"). A bullet is unstructured prose with no such
delimiter, so the question here is instead "does this word or phrase occur
as a whole word in this sentence," which is answered with a word-boundary
match against every known form of the skill (`core.aliases.synonyms`) --
the canonical name plus its aliases, so a bullet that says "Postgres" still
evidences a skills list that says "PostgreSQL." Using the delimited-item
rule here would refuse that alias match outright (a bullet is not a
comma-separated list to split), and using bare substring containment here
-- the mistake the delimited rule exists to correct in the other module --
would let "Go" be evidenced by "Going," exactly the defect class this
project has hit most often.

**Why not Python's `\\b`.** `\\b` is a transition between a word character
and a non-word character, and it is symmetric: it does not require the
*word* side of the transition to be the side the pattern is anchored to.
A skill form ending in a non-word character -- "C++", say -- puts `\\b`
immediately after a `+`, which is itself non-word; if the text that follows
is also non-word (a space, or the end of the sentence, which is the
ordinary case), there is no word/non-word transition there at all, and the
trailing `\\b` fails to match a plainly correct occurrence. Matching is done
instead with explicit lookaround, `(?<![A-Za-z0-9])` and `(?![A-Za-z0-9])`,
which asks the only question that actually matters -- is the character
immediately outside the match alphanumeric -- independent of which
character is on the pattern's own edge. Verified directly: `\\bC\\+\\+\\b`
fails to match `"C++ is great"`, while the lookaround form matches it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from core.aliases import canonicalize, synonyms
from core.resume.models import ResumeDoc

# Neither side of a match may be alphanumeric, or it isn't a whole-word
# occurrence -- see the module docstring's "Why not Python's `\b`" note for
# why this is spelled with lookaround rather than `\b` itself.
_NOT_ALNUM = "[A-Za-z0-9]"


@dataclass
class SkillsResult:
    """The document's skills, partitioned by whether grounded text backs them.

    `listed_only` and `evidenced` are exhaustive and disjoint over `listed`
    (every listed skill is in exactly one of the two). `evidenced_ratio` is
    `1.0` when `listed` is empty: a document that claims no skills has
    nothing unevidenced to report, and `0.0` would read, to anyone
    skimming a report, as "every skill on this resume is unsupported,"
    which is a different and false claim about a document that never made
    one in the first place.
    """

    # `default_factory=list[str]`, not bare `list` -- pyright strict can't
    # bind a bare `list` factory against a `list[str]` target without
    # widening the field to `list[Unknown]`, the same reasoning as
    # `Fact.derived` in `core.profile.models`.
    listed: list[str] = field(default_factory=list[str])
    evidenced: list[str] = field(default_factory=list[str])
    listed_only: list[str] = field(default_factory=list[str])
    evidenced_ratio: float = 1.0
    findings: list[str] = field(default_factory=list[str])


def _mentions(form: str, text: str) -> bool:
    """Whether `form` occurs in `text` as a whole word or phrase.

    Case-insensitive, and anchored with lookaround rather than `\\b` -- see
    the module docstring. `re.escape` is required, not a convenience: a
    skill name is caller-supplied free text, not a hand-written pattern,
    and one containing a regex metacharacter (`C++`, `Node.js`) must be
    matched literally rather than interpreted.

    `form` is never blank here. `synonyms(canonicalize(skill))` only ever
    returns non-blank forms for a non-blank `skill`, and `Section`'s own
    field validator (`core.resume.models.Section._skills_are_single_names`)
    refuses a blank skill before it can reach a document at all -- an empty
    pattern would make the lookaround on both sides degenerate to a
    zero-width assertion between any two adjacent non-alphanumeric
    characters, matching the first `", "` or double space in almost any
    prose. This function does not guard against that state; the guard
    belongs where the value is created, not repeated at every place that
    later reads it.
    """
    pattern = re.compile(
        rf"(?<!{_NOT_ALNUM}){re.escape(form)}(?!{_NOT_ALNUM})",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _named_in(skill: str, texts: Sequence[str]) -> bool:
    """Whether any of `skill`'s known forms -- its canonical name plus every
    alias `core.aliases.synonyms` knows for it -- occurs as a whole word in
    any of `texts`."""
    forms = synonyms(canonicalize(skill))
    return any(_mentions(form, text) for text in texts for form in forms)


def _all_skills(doc: ResumeDoc) -> list[str]:
    """Every skill declared anywhere in `doc`, in declared order, with a
    skill repeated -- in one section or across two -- counted once.

    Deduplication is by canonical form, not literal text, so listing
    "Postgres" in one section and "PostgreSQL" in another is recognised as
    the same repeated claim rather than two independent ones; the text kept
    for a repeat is whichever spelling was declared first, since that is
    the one a reader of the rendered document actually sees first.
    """
    seen: set[str] = set()
    out: list[str] = []
    for section in doc.sections:
        for skill in section.skills:
            canonical = canonicalize(skill)
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append(skill)
    return out


def _all_bullet_texts(doc: ResumeDoc) -> list[str]:
    """Every bullet's text across the whole document, in declared order."""
    return [
        bullet.text
        for section in doc.sections
        for entry in section.entries
        for bullet in entry.bullets
    ]


def check_skills(doc: ResumeDoc, grounded_bullets: Sequence[str]) -> SkillsResult:
    """Partition `doc`'s declared skills into evidenced and listed-only.

    A skill is evidenced when one of its known forms (see
    `core.aliases.synonyms`) occurs as a whole word in at least one of
    `grounded_bullets`. Every other listed skill is listed-only --
    including one named only in a bullet that `grounded_bullets` does not
    contain, which is the check's whole point: a bullet that did not pass
    grounding is not evidence, no matter what it happens to mention.

    That specific case -- named, but only in a bullet this call was not
    given as grounded -- gets its own finding rather than disappearing into
    `listed_only` silently, because it names something actionable a
    generic "not evidenced" does not: the skill has a bullet to point a
    reader at, and the reason it doesn't count is the bullet's own
    grounding, not an absent claim. A skill named nowhere in the document
    at all gets no such finding -- `listed_only` already says everything
    there is to say about it, since there is no specific bullet to name.
    """
    listed = _all_skills(doc)
    if not listed:
        return SkillsResult()

    grounded = list(grounded_bullets)
    ungrounded = [text for text in _all_bullet_texts(doc) if text not in set(grounded)]

    evidenced: list[str] = []
    listed_only: list[str] = []
    findings: list[str] = []
    for skill in listed:
        if _named_in(skill, grounded):
            evidenced.append(skill)
            continue
        listed_only.append(skill)
        if _named_in(skill, ungrounded):
            findings.append(
                f"{skill!r} is named in a bullet that did not pass grounding, "
                "so it is listed but not evidenced"
            )

    return SkillsResult(
        listed=listed,
        evidenced=evidenced,
        listed_only=listed_only,
        evidenced_ratio=len(evidenced) / len(listed),
        findings=findings,
    )
