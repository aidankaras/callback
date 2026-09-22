"""A posting on an employer's own careers page, read from its structured data.

Most employers not on Greenhouse, Lever or Ashby still publish each posting
as `schema.org/JobPosting` JSON-LD, because that is what search engines'
job listings read. That block is the employer's own statement of the
posting, so it is read and nothing else on the page is: no layout scraping,
no following links.

`PageFetcher` fetches exactly one URL the candidate supplied, under rules
stricter than a browser's:

- HTTPS only, and never a platform in `DENIED_DOMAINS`.
- Never a host that resolves to a private, loopback or link-local address:
  a URL copied from a posting must not reach the candidate's own network.
- `robots.txt` is honoured for our User-Agent. A robots file that is missing
  (any 4xx) allows; one that cannot be read for another reason refuses.
- A redirect is followed only on the same host, and re-checked.
- The response must be HTML, under `MAX_PAGE_BYTES`, and at least
  `MIN_INTERVAL` seconds apart per host.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from core.discovery.ats import html_to_text
from core.discovery.fetch import (
    MIN_INTERVAL,
    TIMEOUT,
    USER_AGENT,
    DiscoveryError,
    denied_domain,
)
from core.discovery.models import Posting

MAX_PAGE_BYTES = 5 * 1024 * 1024
Resolver = Callable[[str], list[str]]


def _addresses(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443)})
    except OSError as exc:
        raise DiscoveryError(f"cannot resolve {host}: {exc}") from exc


def _check_page_url(url: str, resolver: Resolver) -> str:
    """The lower-cased host of `url`, or `DiscoveryError` naming why it may not be fetched."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    forbidden = denied_domain(host)
    if forbidden:
        raise DiscoveryError(
            f"{forbidden} does not allow automated access; find the posting on the "
            "employer's own careers site, or paste its text"
        )
    if parts.scheme != "https" or not host:
        raise DiscoveryError(f"{url!r} is not an https URL")
    for address in resolver(host):
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise DiscoveryError(f"{host} resolves to a non-public address ({address})")
    return host


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str, resolver: Resolver) -> None:
        self.host = host
        self.resolver = resolver

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        if _check_page_url(newurl, self.resolver) != self.host:
            raise DiscoveryError(f"{req.full_url} redirects off its host, to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


class PageFetcher:
    """Fetches one careers page at a time, politely, under the rules above."""

    def __init__(self, resolver: Resolver = _addresses) -> None:
        self._resolver = resolver
        self._last: dict[str, float] = {}
        self._extra: list[urllib.request.BaseHandler] = []

    def _open(self, url: str, host: str, limit: int) -> tuple[int, str, bytes]:
        """`(status, content type, body)`; an HTTP error status is returned, not raised."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last.get(host, float("-inf")))
        if wait > 0:
            time.sleep(wait)
        opener = urllib.request.build_opener(_SameHostRedirects(host, self._resolver), *self._extra)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                return (
                    int(response.status),
                    str(response.headers.get("Content-Type", "")),
                    response.read(limit + 1),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, "", b""
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            raise DiscoveryError(f"could not fetch {url}: {exc}") from exc
        finally:
            self._last[host] = time.monotonic()

    def _allowed_by_robots(self, url: str, host: str) -> bool:
        parts = urlsplit(url)
        robots_url = urlunsplit(("https", parts.netloc, "/robots.txt", "", ""))
        status, _, body = self._open(robots_url, host, 512 * 1024)
        if 400 <= status < 500:
            return True
        if status != 200:
            raise DiscoveryError(f"{robots_url} could not be read (status {status}); not fetching")
        rules = urllib.robotparser.RobotFileParser()
        rules.parse(body.decode("utf-8", errors="replace").splitlines())
        return rules.can_fetch(USER_AGENT, url)

    def get_page(self, url: str) -> str:
        """The HTML at `url`, or `DiscoveryError` saying why it was not fetched."""
        host = _check_page_url(url, self._resolver)
        if not self._allowed_by_robots(url, host):
            raise DiscoveryError(f"{host}'s robots.txt does not allow fetching {url}")
        status, content_type, body = self._open(url, host, MAX_PAGE_BYTES)
        if status != 200:
            raise DiscoveryError(f"{url} returned status {status}")
        if "html" not in content_type.lower():
            raise DiscoveryError(f"{url} is not an HTML page ({content_type or 'no type'})")
        if len(body) > MAX_PAGE_BYTES:
            raise DiscoveryError(f"{url} returned more than {MAX_PAGE_BYTES} bytes")
        return body.decode("utf-8", errors="replace")


class _JsonLd(HTMLParser):
    """The text of every `<script type="application/ld+json">` element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and any(
            k == "type" and (v or "").strip().lower() == "application/ld+json" for k, v in attrs
        ):
            self._inside = True
            self.blocks.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.blocks[-1] += data


def _nodes(value: object) -> Iterator[dict[str, object]]:
    """Every object in a JSON-LD value, through lists and `@graph`."""
    if isinstance(value, list):
        for item in cast("list[object]", value):
            yield from _nodes(item)
    elif isinstance(value, dict):
        node = cast("dict[str, object]", value)
        yield node
        yield from _nodes(node.get("@graph"))


def _is_job_posting(node: dict[str, object]) -> bool:
    kind = node.get("@type")
    kinds = cast("list[object]", kind) if isinstance(kind, list) else [kind]
    return any(str(k).rsplit("/", 1)[-1] == "JobPosting" for k in kinds)


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _name(value: object) -> str:
    if isinstance(value, dict):
        return _text(cast("dict[str, object]", value).get("name"))
    return _text(value)


def _location(node: dict[str, object]) -> str:
    if _text(node.get("jobLocationType")).upper() == "TELECOMMUTE":
        return "Remote"
    places = node.get("jobLocation")
    places_list = cast("list[object]", places) if isinstance(places, list) else [places]
    found: list[str] = []
    for place in places_list:
        if not isinstance(place, dict):
            continue
        address = cast("dict[str, object]", place).get("address")
        if isinstance(address, dict):
            parts = cast("dict[str, object]", address)
            country = parts.get("addressCountry")
            fields = [
                _text(parts.get("addressLocality")),
                _text(parts.get("addressRegion")),
                _name(country),
            ]
            label = ", ".join(f for f in fields if f)
        else:
            label = _text(address)
        if label and label not in found:
            found.append(label)
    return "; ".join(found)


def _date(value: object) -> datetime | None:
    """A JSON-LD date or datetime, made timezone-aware (UTC when none is given)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_job_postings(markup: str, page_url: str, *, now: datetime) -> list[Posting]:
    """Every `JobPosting` in the page's JSON-LD, as postings.

    A posting whose `validThrough` has passed is left out: the employer has
    said it is closed. A block that is not valid JSON is skipped, since a
    page commonly carries other structured data; `DiscoveryError` is raised
    only when no posting at all is found, saying why.
    """
    parser = _JsonLd()
    parser.feed(markup)
    host = (urlsplit(page_url).hostname or "").lower()
    postings: list[Posting] = []
    closed: list[str] = []
    for block in parser.blocks:
        try:
            data: object = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _nodes(data):
            if not _is_job_posting(node):
                continue
            title = _text(node.get("title"))
            company = _name(node.get("hiringOrganization"))
            description = node.get("description")
            text = html_to_text(description) if isinstance(description, str) else ""
            if not (title and company and text):
                continue
            valid_through = _date(node.get("validThrough"))
            if valid_through is not None and valid_through < now:
                closed.append(f"{title} (closed {valid_through:%Y-%m-%d})")
                continue
            url = _text(node.get("url")) or page_url
            identifier = node.get("identifier")
            key = (
                _text(cast("dict[str, object]", identifier).get("value"))
                if isinstance(identifier, dict)
                else _text(identifier)
            ) or hashlib.sha256(f"{url}\n{title}".encode()).hexdigest()[:16]
            postings.append(
                Posting(
                    id=f"page:{host}:{key}",
                    ats="page",
                    board=host,
                    company=company,
                    title=title,
                    location=_location(node),
                    url=url,
                    apply_url=url,
                    text=text,
                    published_at=_date(node.get("datePosted")),
                )
            )
    if not postings:
        if closed:
            raise DiscoveryError(f"the posting on {page_url} is closed: {'; '.join(closed)}")
        raise DiscoveryError(
            f"{page_url} carries no schema.org JobPosting data; paste the posting's text instead"
        )
    return postings
