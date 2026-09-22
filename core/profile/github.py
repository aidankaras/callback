"""Candidate facts drafted from the candidate's own public GitHub repositories.

A repository's description is the candidate's own writing about their own
work, so it is a reasonable first draft of a project fact. It is only a
draft: every fact here is written unconfirmed, to a file, never to the
store. The candidate reads the file, edits what is wrong, adds it with
`callback facts add` and confirms each fact at their own terminal, exactly
as for an imported resume.

A fork is someone else's project and is skipped; so is a repository with no
description, which would give a fact no text to ground a bullet in.

The entities are the repository's name and its primary language, which
GitHub computes from the code the repository holds. Topics are not used:
they are tags anyone can set, not evidence of what the code does, and an
entity is what lets a bullet name a technology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import cast

from core.aliases import canonicalize
from core.discovery.fetch import DiscoveryError, Fetcher
from core.profile.models import Dates, Fact, FactKind, Provenance, Trust

GITHUB_API = "api.github.com"
_USER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_MONTH = re.compile(r"(\d{4}-\d{2})")


@dataclass
class Drafted:
    facts: list[Fact] = field(default_factory=list[Fact])
    skipped: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


def repos_url(user: str) -> str:
    """The public listing of the repositories `user` owns (the first 100)."""
    if not _USER.fullmatch(user):
        raise DiscoveryError(f"{user!r} is not a GitHub username")
    return f"https://{GITHUB_API}/users/{user}/repos?per_page=100&type=owner&sort=pushed"


def _month(value: object) -> str | None:
    match = _MONTH.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def draft_facts(user: str, payload: object) -> Drafted:
    """Unconfirmed project facts from a `repos` listing payload for `user`."""
    if not isinstance(payload, list):
        raise DiscoveryError("GitHub returned a payload in a shape this reader does not know")
    drafted = Drafted()
    for item in cast("list[object]", payload):
        if not isinstance(item, dict):
            raise DiscoveryError("GitHub returned a payload in a shape this reader does not know")
        repo = cast("dict[str, object]", item)
        name = str(repo.get("name") or "")
        if not name:
            continue
        if repo.get("fork"):
            drafted.skipped.append((name, "a fork is someone else's project"))
            continue
        description = " ".join(str(repo.get("description") or "").split())
        if not description:
            drafted.skipped.append((name, "no description to draft a fact from"))
            continue
        entities = [name]
        language = repo.get("language")
        if isinstance(language, str) and language:
            entities.append(canonicalize(language))
        start, end = _month(repo.get("created_at")), _month(repo.get("pushed_at"))
        drafted.facts.append(
            Fact(
                id=f"gh-{_slug(name)}",
                kind=FactKind.PROJECT,
                text=f"{name}: {description}",
                entities=list(dict.fromkeys(entities)),
                dates=Dates(start=start, end=end) if start else None,
                provenance=Provenance(
                    source=f"github:{repo.get('full_name') or f'{user}/{name}'}",
                    trust=Trust.IMPORT_UNCONFIRMED,
                ),
            )
        )
    return drafted


def fetch_drafts(user: str, fetcher: Fetcher) -> Drafted:
    """Read `user`'s public repositories and draft a fact for each."""
    return draft_facts(user, fetcher.get_json(repos_url(user)))
