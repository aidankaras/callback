"""Field briefs: what screeners in one field look for, and how its writing reads.

A brief is written by the tailor skill from employer-published material and
career-centre samples, reviewed by the candidate, and stored at
`briefs/<field>.yaml` under the private data directory. Nothing here judges
whether its advice is good; this module only holds a brief to the rules that
make it checkable: every claim and exemplar cites a source listed beside it,
no source is a platform that forbids automated access, and every exemplar is
a short quotation rather than a reproduction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import AwareDatetime, BaseModel, Field, ValidationError, field_validator

from core.discovery.fetch import denied_domain
from core.paths import OutsideHomeError, resolve_inside

FIELD = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MAX_EXEMPLAR_WORDS = 60
MIN_EXEMPLARS = 5
# A source older than this is due for a refresh: what a field screens for
# moves, and a brief that stops being refreshed reads as current regardless.
STALE_AFTER = timedelta(days=90)


class BriefError(ValueError):
    """A brief that could not be read: missing, unparseable, or invalid."""


class Source(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    kind: Literal["employer", "career-centre"]
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    fetched_at: AwareDatetime

    @field_validator("url")
    @classmethod
    def _plain_host(cls, v: str) -> str:
        host = urlsplit(v).hostname
        if not host or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.?", host):
            raise ValueError(f"source url {v!r} has no plain host name")
        return v


class Claim(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    text: str = Field(min_length=1)
    source: str


class Exemplar(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    kind: Literal["bullet", "letter-paragraph"]
    text: str = Field(min_length=1)
    source: str
    why: str = Field(min_length=1)


class Brief(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    field: str = Field(pattern=FIELD.pattern)
    built_at: AwareDatetime
    sources: tuple[Source, ...] = Field(min_length=1)
    claims: tuple[Claim, ...] = ()
    exemplars: tuple[Exemplar, ...] = ()
    phrases_to_avoid: tuple[str, ...] = ()

    @field_validator("phrases_to_avoid")
    @classmethod
    def _phrases_have_words(cls, phrases: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse a blank phrase: it compiles to a pattern that matches
        between any two non-word characters, failing every sentence that
        has punctuation in it."""
        if any(not p.split() for p in phrases):
            raise ValueError("a phrase to avoid cannot be blank")
        return phrases


@dataclass(frozen=True)
class BriefReport:
    fails: tuple[str, ...] = ()
    warns: tuple[str, ...] = ()

    @property
    def verdict(self) -> Literal["pass", "warn", "fail"]:
        if self.fails:
            return "fail"
        return "warn" if self.warns else "pass"


def load_brief(field: str) -> Brief:
    """The stored brief for `field`; raises `BriefError` naming what is wrong."""
    if not FIELD.fullmatch(field):
        raise BriefError(f"brief name {field!r} must be lowercase letters, digits and hyphens")
    try:
        path = resolve_inside(f"briefs/{field}.yaml")
    except OutsideHomeError as exc:
        raise BriefError(f"brief {field!r} resolves outside the callback home") from exc
    try:
        brief = Brief.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise BriefError(f"no brief named {field!r} at {path}") from exc
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise BriefError(f"cannot read brief {field!r}: {exc}") from exc
    if brief.field != field:
        raise BriefError(f"{path} declares field {brief.field!r}, not {field!r}")
    return brief


def check_brief(brief: Brief, *, now: datetime) -> BriefReport:
    """Every rule `brief` breaks: unknown or denied sources and over-long
    exemplars fail; a stale source or too few exemplars warn."""
    fails: list[str] = []
    warns: list[str] = []
    ids = [s.id for s in brief.sources]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        fails.append(f"source id {dup!r} is listed more than once")
    for s in brief.sources:
        denied = denied_domain(urlsplit(s.url).hostname or "")
        if denied:
            fails.append(f"source {s.id!r} is on {denied}, which forbids automated access")
        if now - s.fetched_at > STALE_AFTER:
            warns.append(f"source {s.id!r} was fetched {(now - s.fetched_at).days} days ago")
    cited = [("claim", n, c.source) for n, c in enumerate(brief.claims)]
    cited += [("exemplar", n, e.source) for n, e in enumerate(brief.exemplars)]
    fails += [
        f"{what} {n} cites unknown source {src!r}" for what, n, src in cited if src not in ids
    ]
    for n, e in enumerate(brief.exemplars):
        words = len(e.text.split())
        if words > MAX_EXEMPLAR_WORDS:
            fails.append(f"exemplar {n} is {words} words; at most {MAX_EXEMPLAR_WORDS}")
    if len(brief.exemplars) < MIN_EXEMPLARS:
        warns.append(f"{len(brief.exemplars)} exemplars; at least {MIN_EXEMPLARS} expected")
    return BriefReport(tuple(fails), tuple(warns))
