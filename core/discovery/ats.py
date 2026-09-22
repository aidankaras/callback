"""The three public posting APIs: where they are, and what they return.

Each parser maps its API's own fields onto `Posting` and refuses a payload
whose shape it does not recognise, rather than reading it as a board with
no openings -- an API that changes shape must not look like a company that
stopped hiring.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from core.discovery.fetch import DiscoveryError, denied_domain
from core.discovery.models import Ats, Posting

_BOARD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_BLOCKS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
_SKIP = {"script", "style"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skipping += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Plain text of `markup`: tags dropped, block elements on their own lines."""
    parser = _Text()
    parser.feed(markup)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _board(board: str) -> str:
    if not _BOARD.fullmatch(board):
        raise DiscoveryError(f"board {board!r} is not a valid board name")
    return board


def posting_url_for(ats: Ats, board: str) -> str:
    """The documented public endpoint listing every posting on `board`."""
    board = _board(board)
    if ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    if ats == "lever":
        return f"https://api.lever.co/v0/postings/{board}?mode=json"
    return f"https://api.ashbyhq.com/posting-api/job-board/{board}"


_POSTING_URLS: tuple[tuple[Ats, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"^(?:job-boards|boards)\.greenhouse\.io$")),
    ("lever", re.compile(r"^jobs\.lever\.co$")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$")),
)


def resolve_url(url: str) -> tuple[Ats, str, str]:
    """`(ats, board, job id)` for a posting's public URL.

    A URL on a platform that forbids automated access is refused by name,
    so the caller can say why rather than report it as unrecognised.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    forbidden = denied_domain(host)
    if forbidden:
        raise DiscoveryError(
            f"{forbidden} does not allow automated access; find the posting on the "
            "employer's own careers site, or paste its text"
        )
    segments = [s for s in parts.path.split("/") if s]
    for ats, pattern in _POSTING_URLS:
        if not pattern.match(host):
            continue
        if ats == "greenhouse" and len(segments) >= 3 and segments[1] == "jobs":
            return ats, _board(segments[0]), segments[2]
        if ats != "greenhouse" and len(segments) >= 2:
            return ats, _board(segments[0]), segments[1]
    raise DiscoveryError(f"{url!r} is not a Greenhouse, Lever or Ashby posting URL")


def _when(value: object) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _records(ats: Ats, payload: object) -> list[dict[str, object]]:
    """The list of posting records in `payload`: a bare list for Lever, the
    `jobs` member of an object for Greenhouse and Ashby."""
    items: object = payload
    if ats != "lever":
        items = (
            cast("dict[str, object]", payload).get("jobs") if isinstance(payload, dict) else None
        )
    if not isinstance(items, list):
        raise DiscoveryError(f"{ats} returned a payload in a shape this reader does not know")
    records = cast("list[object]", items)
    if not all(isinstance(r, dict) for r in records):
        raise DiscoveryError(f"{ats} returned a payload in a shape this reader does not know")
    return cast("list[dict[str, object]]", records)


def parse_board(ats: Ats, board: str, company: str, payload: object) -> list[Posting]:
    """Every listed posting in an API `payload` for `board`."""
    postings: list[Posting] = []
    try:
        for r in _records(ats, payload):
            if ats == "greenhouse":
                location = cast("dict[str, object]", r.get("location") or {})
                postings.append(
                    Posting(
                        id=f"greenhouse:{board}:{r['id']}",
                        ats=ats,
                        board=board,
                        company=company,
                        title=str(r["title"]),
                        location=str(location.get("name") or ""),
                        url=str(r["absolute_url"]),
                        apply_url=str(r["absolute_url"]),
                        text=html_to_text(html.unescape(str(r.get("content") or ""))),
                        published_at=_when(r.get("updated_at")),
                    )
                )
            elif ats == "lever":
                categories = cast("dict[str, object]", r.get("categories") or {})
                sections = [str(r.get(k) or "") for k in ("descriptionPlain", "additionalPlain")]
                postings.append(
                    Posting(
                        id=f"lever:{board}:{r['id']}",
                        ats=ats,
                        board=board,
                        company=company,
                        title=str(r["text"]),
                        location=str(categories.get("location") or ""),
                        url=str(r["hostedUrl"]),
                        apply_url=str(r.get("applyUrl") or r["hostedUrl"]),
                        text="\n\n".join(s.strip() for s in sections if s.strip()),
                        published_at=_when(r.get("createdAt")),
                    )
                )
            else:
                if r.get("isListed") is False:
                    continue
                postings.append(
                    Posting(
                        id=f"ashby:{board}:{r['id']}",
                        ats=ats,
                        board=board,
                        company=company,
                        title=str(r["title"]),
                        location=str(r.get("location") or ""),
                        url=str(r["jobUrl"]),
                        apply_url=str(r.get("applyUrl") or r["jobUrl"]),
                        text=str(r.get("descriptionPlain") or "").strip(),
                        published_at=_when(r.get("publishedAt")),
                    )
                )
    except (KeyError, ValueError, ValidationError) as exc:
        raise DiscoveryError(f"{ats} returned a posting this reader cannot read: {exc}") from exc
    return postings
