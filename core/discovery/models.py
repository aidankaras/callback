"""A posting as read from an employer's own board."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Ats = Literal["greenhouse", "lever", "ashby", "hn"]


class Posting(BaseModel):
    """One job posting, from the employer's applicant-tracking system.

    `text` is the posting's own description as plain text; `id` is stable
    across fetches (`<ats>:<board>:<job id>`), which is what lets a watch
    tell a new posting from one it has already seen.
    """

    model_config = {"extra": "forbid", "frozen": True}

    id: str
    ats: Ats
    board: str
    company: str
    title: str
    location: str = ""
    url: str
    apply_url: str = ""
    text: str
    published_at: datetime | None = None
