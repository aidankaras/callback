"""A posting as read from an employer's own board."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, field_validator

Ats = Literal["greenhouse", "lever", "ashby", "hn"]
# Where a posting was read from: a watchable board, one careers page, or the
# candidate, for a programme no board or page publishes in a readable form.
Source = Literal["greenhouse", "lever", "ashby", "hn", "page", "manual"]


def normal_url(url: str) -> str:
    """The URL as one page has one spelling: scheme and host lower-cased, no
    fragment, one trailing slash dropped from a non-root path, query kept,
    because a Greenhouse posting is told apart only by its `?gh_jid=`. Text
    that does not split as a URL is returned stripped, to compare as itself."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    path = parts.path[:-1] if len(parts.path) > 1 and parts.path.endswith("/") else parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


class Posting(BaseModel):
    """One job posting, from the employer's applicant-tracking system.

    `text` is the posting's own description as plain text; `id` is stable
    across fetches (`<ats>:<board>:<job id>`), which is what lets a watch
    tell a new posting from one it has already seen. `deadline` is known only
    for a posting the candidate added, and must carry a timezone: a deadline
    read in the wrong zone is off by hours on the day it matters.
    """

    model_config = {"extra": "forbid", "frozen": True}

    id: str
    ats: Source
    board: str
    company: str
    title: str
    location: str = ""
    url: str
    apply_url: str = ""
    text: str
    published_at: datetime | None = None
    deadline: datetime | None = None

    @field_validator("deadline")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("a deadline must carry a timezone")
        return value
