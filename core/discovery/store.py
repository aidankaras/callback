"""Targets, criteria and discovered postings, kept under the data home.

- `targets.yaml` -- the companies to watch, each by its applicant-tracking
  system and board name.
- `criteria.yaml` -- which titles are worth seeing: terms to include and to
  exclude. Required before a watch runs; with none, a watch would store
  every posting on every board.
- `postings.json` -- every matching posting found, with when it was first
  seen. That date matters: applying in a posting's first days is one of the
  strongest predictors of hearing back, and a watch run on a schedule is
  what makes it knowable.

`take` copies a posting's own text into `inbox/<job>.md`, headed
`# <title> - <company>` -- the posting a resume and cover letter are then
checked against, straight from the employer rather than retyped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, Field, ValidationError

from core.discovery.ats import parse_board, posting_url_for, resolve_url
from core.discovery.fetch import DiscoveryError, Fetcher
from core.discovery.models import Ats, Posting
from core.paths import resolve_inside, write_private

TARGETS_FILE = "targets.yaml"
CRITERIA_FILE = "criteria.yaml"
POSTINGS_FILE = "postings.json"


class Target(BaseModel):
    """A company to watch, by where it publishes its postings."""

    model_config = {"extra": "forbid", "frozen": True}

    company: str = Field(min_length=1)
    ats: Ats
    board: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _term(term: str) -> re.Pattern[str]:
    words = " ".join(term.casefold().split())
    if words.endswith("*"):
        return re.compile(r"(?<!\w)" + re.escape(words.rstrip("* ")))
    return re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)")


class Criteria(BaseModel):
    """Which posting titles are worth seeing.

    A title matches when it contains an `include` term and no `exclude`
    term. Terms are case-insensitive whole words -- "intern" matches "ML
    Intern" but not "International", and "ml" does not match "HTML" -- and a
    trailing `*` makes a term a prefix: "intern*" also matches "Internship",
    "data scien*" matches "Data Science".
    """

    model_config = {"extra": "forbid"}

    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)

    def _hit(self, terms: list[str], title: str) -> bool:
        return any(_term(t).search(title.casefold()) for t in terms if t.strip(" *"))

    def matches(self, title: str) -> bool:
        return self._hit(self.include, title) and not self._hit(self.exclude, title)


class Found(Posting):
    """A posting as stored: the posting plus when a watch first saw it."""

    first_seen: datetime


@dataclass
class WatchResult:
    new: list[Found] = field(default_factory=list[Found])
    errors: list[tuple[Target, str]] = field(default_factory=list[tuple[Target, str]])


def _read_yaml(name: str) -> object:
    path = resolve_inside(name)
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DiscoveryError(f"{path} is not readable: {exc}") from exc


def load_targets() -> list[Target]:
    parsed = _read_yaml(TARGETS_FILE)
    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise DiscoveryError(f"{TARGETS_FILE} must be a list of targets")
    try:
        return [Target.model_validate(t) for t in cast("list[object]", parsed)]
    except ValidationError as exc:
        raise DiscoveryError(f"{TARGETS_FILE} holds an invalid target: {exc}") from exc


def add_target(target: Target) -> bool:
    """Add `target` unless it is already watched; return whether it was added."""
    targets = load_targets()
    if any((t.ats, t.board) == (target.ats, target.board) for t in targets):
        return False
    body = yaml.safe_dump([t.model_dump(mode="json") for t in [*targets, target]], sort_keys=False)
    write_private(resolve_inside(TARGETS_FILE), body)
    return True


def save_criteria(criteria: Criteria) -> None:
    write_private(
        resolve_inside(CRITERIA_FILE), yaml.safe_dump(criteria.model_dump(), sort_keys=False)
    )


def load_criteria() -> Criteria:
    parsed = _read_yaml(CRITERIA_FILE)
    if parsed is None:
        raise DiscoveryError(
            f"no search criteria: set {CRITERIA_FILE} (include and exclude terms) before watching, "
            "or every posting on every board would be stored"
        )
    try:
        return Criteria.model_validate(parsed)
    except ValidationError as exc:
        raise DiscoveryError(f"{CRITERIA_FILE} is invalid: {exc}") from exc


def load_postings() -> dict[str, Found]:
    path = resolve_inside(POSTINGS_FILE)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("expected an object keyed by posting id")
        return {k: Found.model_validate(v) for k, v in cast("dict[str, object]", raw).items()}
    except (OSError, ValueError, ValidationError) as exc:
        raise DiscoveryError(f"{path} is not readable: {exc}") from exc


def _save_postings(postings: dict[str, Found]) -> None:
    body = {k: v.model_dump(mode="json") for k, v in postings.items()}
    write_private(resolve_inside(POSTINGS_FILE), json.dumps(body, indent=2) + "\n")


def watch(fetcher: Fetcher, *, now: datetime) -> WatchResult:
    """Poll every target and store the matching postings not seen before.

    A board that cannot be read is recorded in `errors` and the rest are
    still polled: one company's outage must not hide another's openings.
    """
    criteria = load_criteria()
    stored = load_postings()
    result = WatchResult()
    for target in load_targets():
        try:
            payload = fetcher.get_json(posting_url_for(target.ats, target.board))
            postings = parse_board(target.ats, target.board, target.company, payload)
        except DiscoveryError as exc:
            result.errors.append((target, str(exc)))
            continue
        for posting in postings:
            if posting.id in stored or not criteria.matches(posting.title):
                continue
            found = Found(**posting.model_dump(), first_seen=now)
            stored[posting.id] = found
            result.new.append(found)
    _save_postings(stored)
    return result


def resolve(url: str, company: str, fetcher: Fetcher, *, now: datetime) -> Found:
    """Fetch the canonical posting behind a Greenhouse, Lever or Ashby URL and store it."""
    ats, board, job_id = resolve_url(url)
    payload = fetcher.get_json(posting_url_for(ats, board))
    wanted = f"{ats}:{board}:{job_id}"
    for posting in parse_board(ats, board, company, payload):
        if posting.id == wanted:
            stored = load_postings()
            found = stored.get(wanted) or Found(**posting.model_dump(), first_seen=now)
            stored[wanted] = found
            _save_postings(stored)
            return found
    raise DiscoveryError(f"{url} is not on {company}'s {ats} board any more")


def take(posting_id: str, job_id: str) -> Path:
    """Save a stored posting's own text as the posting for `job_id`."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", job_id):
        raise DiscoveryError(f"job id {job_id!r} must be lowercase letters, digits and hyphens")
    posting = load_postings().get(posting_id)
    if posting is None:
        raise DiscoveryError(f"no posting {posting_id!r}: run `callback watch` or resolve its URL")
    body = (
        f"# {posting.title} - {posting.company}\n\n"
        f"{posting.url}\n\n"
        f"{posting.location}\n\n"
        f"{posting.text}\n"
    )
    path = resolve_inside(f"inbox/{job_id}.md")
    write_private(path, body)
    return path
