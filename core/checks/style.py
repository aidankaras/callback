"""The style gate: writing that reads like a template rather than like the candidate.

Grounding proves a sentence is backed by the facts it cites; it cannot tell
whether the sentence is any good. This gate catches the three failures that
are decidable without judgement:

- **A stock phrase fails.** `UNCONDITIONAL` holds phrases with no use in a
  resume or letter except as filler. `PHRASE_ONLY` holds words that are
  technical terms in the candidate's fields ("leverage ratio", "robust
  standard errors", "dynamic programming") and are filler only in the
  phrases listed, so only those phrases are matched. A brief may add its
  own. Matching is case-insensitive and whole-word, so "Steam players" is
  not "team players".
- **Copying an exemplar fails.** A brief's exemplars show the field's
  register; a draft that shares `MIN_COPIED_WORDS` consecutive words with one
  has taken its wording, not its structure.
- **A resume bullet with nothing specific warns.** No digit, and none of its
  cited facts' entities in its text. A bullet can be grounded and still say
  nothing a reader can check. It warns rather than fails: some good bullets
  carry neither.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from core.aliases import canonicalize
from core.briefs import Brief
from core.checks.grounding import checkable_entity_tokens
from core.profile.models import Fact
from core.resume.models import ResumeDoc

MIN_COPIED_WORDS = 6

UNCONDITIONAL = (
    r"spearhead(?:s|ed|ing)?",
    r"results[- ]driven",
    r"proven track records?",
    r"passionate about",
    r"cutting[- ]edge",
    r"fast[- ]paced environments?",
    r"team[- ]players?",
    r"detail[- ]oriented",
    r"i(?: am|['\u2019]m) excited to apply",
    r"i am writing to express",
)
PHRASE_ONLY = (
    r"leverag(?:e|es|ed|ing) (?:my|our|the)",
    r"leveraging state[- ]of[- ]the[- ]art",
    r"robust solutions?",
    r"dynamic teams?",
    r"data[- ]driven insights?",
)


def _whole(body: str, edge: str = r"\w") -> re.Pattern[str]:
    return re.compile(rf"(?<!{edge}){body.replace(' ', r'\s+')}(?!{edge})", re.IGNORECASE)


_BUILT_IN = tuple(_whole(p) for p in (*UNCONDITIONAL, *PHRASE_ONLY))


def _literal(phrase: str, edge: str = r"\w") -> re.Pattern[str]:
    return _whole(" ".join(re.escape(w) for w in phrase.split()), edge)


def _entity(name: str) -> re.Pattern[str]:
    # A one-letter name ("R", "C") sits inside compounds like "R-squared",
    # "R&D", "C++" and "C-suite"; those are not the name used on its own.
    return _literal(name, r"[\w&+#-]" if len(name.strip()) == 1 else r"\w")


def stock_phrases(text: str, extra: Iterable[str] = ()) -> list[str]:
    """Every stock phrase in `text`, as written there."""
    patterns = (*_BUILT_IN, *(_literal(p) for p in extra))
    return [m.group(0) for p in patterns for m in p.finditer(text)]


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w%]+", " ", text.casefold()).split()


def copied_run(text: str, sources: Iterable[str], n: int = MIN_COPIED_WORDS) -> str | None:
    """The first run of `n` consecutive words `text` shares with any of `sources`."""
    grams = {
        tuple(words[i : i + n])
        for s in sources
        for words in [_words(s)]
        for i in range(len(words) - n + 1)
    }
    mine = _words(text)
    return next(
        (
            " ".join(mine[i : i + n])
            for i in range(len(mine) - n + 1)
            if tuple(mine[i : i + n]) in grams
        ),
        None,
    )


def is_specific(text: str, cited: Sequence[Fact]) -> bool:
    """Whether `text` carries a digit or names one of its cited facts' entities,
    literally or through the alias table."""
    if any(c.isdigit() for c in text):
        return True
    entities = [e for f in cited for e in f.entities]
    if any(_entity(e).search(text) for e in entities if e.split()):
        return True
    canonical = {canonicalize(e) for e in entities}
    return any(canonicalize(t) in canonical for t in checkable_entity_tokens(text))


def text_findings(label: str, text: str, brief: Brief | None) -> list[str]:
    """The failing findings for one sentence or bullet: stock phrases and copying."""
    extra = brief.phrases_to_avoid if brief else ()
    found = [f"{label}: stock phrase {p!r}" for p in stock_phrases(text, extra)]
    if brief is not None:
        run = copied_run(text, (e.text for e in brief.exemplars))
        if run is not None:
            found.append(f"{label}: copies an exemplar of the {brief.field} brief: {run!r}")
    return found


def resume_findings(
    doc: ResumeDoc, facts: Mapping[str, Fact], brief: Brief | None
) -> tuple[list[str], list[str]]:
    """`(fails, warns)` for every bullet in `doc`: stock phrases and copying
    fail; a bullet with nothing specific warns."""
    fails: list[str] = []
    warns: list[str] = []
    bullets = [b for s in doc.sections for e in s.entries for b in e.bullets]
    for i, bullet in enumerate(bullets):
        fails += text_findings(f"bullet {i}", bullet.text, brief)
        if not is_specific(bullet.text, [facts[s] for s in bullet.sources if s in facts]):
            warns.append(
                f"bullet {i}: no number and none of its cited facts' entities; "
                "name the system, the scale or the result"
            )
    return fails, warns
