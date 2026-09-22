"""A posting on an employer's own careers page, read from its structured data.

Most employers not on Greenhouse, Lever or Ashby still publish each posting
as `schema.org/JobPosting` JSON-LD, because that is what search engines'
job listings read. That block is the employer's own statement of the
posting, so it is read and nothing else on the page is: no layout scraping,
no following links.

`PageFetcher` fetches exactly one URL the candidate supplied, under rules
stricter than a browser's:

- HTTPS on the default port only, with no user name or password, and never
  a platform in `DENIED_DOMAINS`.
- Never a host whose name resolves, at the moment it is checked, to a
  private, loopback or link-local address, or to an IPv6 address embedding
  an IPv4 one: a URL copied from a posting must not reach the candidate's
  own network. The connection resolves the name again, so a host whose DNS
  answer changes in between is not caught; this raises the cost of reaching
  a local address, and does not make it impossible.
- `robots.txt` is honoured for our User-Agent. A robots file that is missing
  (any 4xx) allows; one that cannot be read for another reason refuses.
- A redirect is followed only on the same host and port, and only to a path
  robots.txt allows.
- The response must be HTML, under `MAX_PAGE_BYTES`, and at least
  `MIN_INTERVAL` seconds apart per host.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Iterator
from dataclasses import dataclass
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
_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")
# `is_global` passes these IPv6 forms, which embed an IPv4 address a gateway
# may route to: IPv4-compatible (::/96) and the NAT64 prefix (64:ff9b::/96).
_EMBEDDED_V4 = (ipaddress.ip_network("::/96"), ipaddress.ip_network("64:ff9b::/96"))
Resolver = Callable[[str], list[str]]


def _addresses(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443)})
    except OSError as exc:
        raise DiscoveryError(f"cannot resolve {host}: {exc}") from exc


def _check_page_url(url: str, resolver: Resolver) -> str:
    """The lower-cased host of `url`, or `DiscoveryError` naming why it may not be fetched."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError as exc:
        raise DiscoveryError(f"{url!r} is not a URL: {exc}") from exc
    forbidden = denied_domain(host)
    if forbidden:
        raise DiscoveryError(
            f"{forbidden} does not allow automated access; find the posting on the "
            "employer's own careers site, or paste its text"
        )
    if parts.scheme != "https" or not host:
        raise DiscoveryError(f"{url!r} is not an https URL")
    if parts.username is not None or parts.password is not None:
        raise DiscoveryError(f"{url!r} carries a user name or password")
    if port not in (None, 443):
        raise DiscoveryError(f"{url!r} names port {port}; only the default HTTPS port is fetched")
    for address in resolver(host):
        ip = ipaddress.ip_address(address.split("%")[0])
        embedded = ip.version == 6 and any(ip in net for net in _EMBEDDED_V4)
        if not ip.is_global or embedded:
            raise DiscoveryError(f"{host} resolves to a non-public address ({address})")
    return host


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect must stay on the host, and robots.txt must allow its target
    too: a same-host redirect is otherwise a way into a disallowed path."""

    def __init__(self, host: str, resolver: Resolver, permits: Callable[[str], bool]) -> None:
        self.host = host
        self.resolver = resolver
        self.permits = permits

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
        if not self.permits(newurl):
            raise DiscoveryError(
                f"{req.full_url} redirects to {newurl}, which robots.txt disallows"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


class PageFetcher:
    """Fetches one careers page at a time, politely, under the rules above."""

    def __init__(self, resolver: Resolver = _addresses) -> None:
        self._resolver = resolver
        self._last: dict[str, float] = {}
        self._extra: list[urllib.request.BaseHandler] = []
        # Per host: its parsed rules, or None when it has no robots file.
        self._rules: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _open(self, url: str, host: str, limit: int) -> tuple[int, str, bytes]:
        """`(status, content type, body)`; an HTTP error status is returned, not raised."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last.get(host, float("-inf")))
        if wait > 0:
            time.sleep(wait)
        opener = urllib.request.build_opener(
            _SameHostRedirects(host, self._resolver, self._permits), *self._extra
        )
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
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ValueError,
            OSError,
        ) as exc:
            raise DiscoveryError(f"could not fetch {url}: {exc}") from exc
        finally:
            self._last[host] = time.monotonic()

    def _permits(self, url: str) -> bool:
        """Whether the robots rules already read for `url`'s host allow it."""
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if host not in self._rules:
            return False
        rules = self._rules[host]
        return rules is None or rules.can_fetch(USER_AGENT, url)

    def _allowed_by_robots(self, url: str, host: str) -> bool:
        if host not in self._rules:
            robots_url = urlunsplit(("https", host, "/robots.txt", "", ""))
            # The robots file itself may be redirected; that fetch is judged
            # against no rules at all, which is what "not yet read" means.
            self._rules[host] = None
            try:
                status, _, body = self._open(robots_url, host, 512 * 1024)
            except BaseException:
                # Unread is not "no robots file": leave nothing cached.
                del self._rules[host]
                raise
            if 400 <= status < 500:
                self._rules[host] = None
            elif status != 200:
                del self._rules[host]
                raise DiscoveryError(
                    f"{robots_url} could not be read (status {status}); not fetching"
                )
            else:
                rules = urllib.robotparser.RobotFileParser()
                rules.parse(body.decode("utf-8", errors="replace").splitlines())
                self._rules[host] = rules
        return self._permits(url)

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


def _clean(value: str) -> str:
    """`value` with lone surrogates replaced, so it can be written as UTF-8."""
    return value.encode("utf-8", "replace").decode("utf-8")


def _text(value: object) -> str:
    return " ".join(_clean(value).split()) if isinstance(value, str) else ""


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


def _date(value: object, *, end_of_day: bool = False) -> datetime | None:
    """A JSON-LD date or datetime, timezone-aware (UTC when none is given).

    `None` when absent; `ValueError` when present but unreadable, so a caller
    deciding whether a posting is open never reads "unreadable" as "open".
    A date with no time is the start of that day, or with `end_of_day` its
    last moment: a posting valid through 1 October is open all of 1 October.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not a date")
    text = value.strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if end_of_day and len(text) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _page_key(url: str) -> tuple[str, str]:
    """What identifies a page: host and path, without query, fragment or trailing slash."""
    parts = urlsplit(url.strip())
    return (parts.hostname or "").lower().rstrip("."), parts.path.rstrip("/")


@dataclass(frozen=True)
class Listed:
    """A posting as the page lists it, with what the page says about its validity."""

    posting: Posting
    valid_through: datetime | None
    unreadable_validity: str | None = None

    def closed_reason(self, now: datetime) -> str | None:
        if self.unreadable_validity is not None:
            return (
                f"its validThrough {self.unreadable_validity!r} cannot be read, so whether "
                "it is still open is unknown"
            )
        if self.valid_through is not None and self.valid_through < now:
            return f"it closed {self.valid_through:%Y-%m-%d}"
        return None


def read_postings(markup: str, page_url: str) -> list[Listed]:
    """Every `JobPosting` in the page's JSON-LD, open or not.

    A block that is not valid JSON, or that the decoder cannot hold (nesting
    too deep, an integer too long), is skipped: a page commonly carries other
    structured data, and one odd block must not cost the posting beside it.
    A posting's `url` is kept only when it is on the page's own host and not
    a denied platform; otherwise the page's URL stands in for it.
    """
    parser = _JsonLd()
    parser.feed(markup)
    host = _page_key(page_url)[0]
    listed: list[Listed] = []
    for block in parser.blocks:
        try:
            data: object = json.loads(block)
        except (ValueError, RecursionError):
            continue
        for node in _nodes(data):
            if not _is_job_posting(node):
                continue
            title = _text(node.get("title"))
            company = _name(node.get("hiringOrganization"))
            description = node.get("description")
            text = html_to_text(_clean(description)) if isinstance(description, str) else ""
            if not (title and company and text):
                continue
            stated = _text(node.get("url"))
            try:
                stated_host = _page_key(stated)[0] if stated else ""
            except ValueError:
                stated_host = ""
            url = stated if stated_host == host and not denied_domain(stated_host) else page_url
            identifier = node.get("identifier")
            ident = (
                _text(cast("dict[str, object]", identifier).get("value"))
                if isinstance(identifier, dict)
                else _text(identifier)
            )
            # Sites reuse one identifier across postings (often the company's
            # name), so the page's own path is part of the key.
            digest = hashlib.sha256("\n".join(_page_key(url)).encode()).hexdigest()[:10]
            key = f"{_SAFE_KEY.sub('-', ident)[:40]}-{digest}" if ident else digest
            try:
                valid_through, unreadable = (
                    _date(node.get("validThrough"), end_of_day=True),
                    None,
                )
            except ValueError:
                valid_through, unreadable = None, str(node.get("validThrough"))[:60]
            try:
                published = _date(node.get("datePosted"))
            except ValueError:
                published = None
            listed.append(
                Listed(
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
                        published_at=published,
                    ),
                    valid_through,
                    unreadable,
                )
            )
    return listed


def _no_data(page_url: str) -> DiscoveryError:
    return DiscoveryError(
        f"{page_url} carries no schema.org JobPosting data; paste the posting's text instead"
    )


def parse_job_postings(markup: str, page_url: str, *, now: datetime) -> list[Posting]:
    """Every open `JobPosting` on the page; `DiscoveryError` when there is none, saying why."""
    listed = read_postings(markup, page_url)
    if not listed:
        raise _no_data(page_url)
    open_ = [item.posting for item in listed if item.closed_reason(now) is None]
    if not open_:
        reasons = "; ".join(f"{i.posting.title}: {i.closed_reason(now)}" for i in listed)
        raise DiscoveryError(f"no posting on {page_url} is open: {reasons}")
    return open_


def posting_at(markup: str, url: str, *, now: datetime) -> Posting:
    """The one posting the candidate's `url` names.

    A page listing one posting is taken to be it. A page listing several must
    have one whose URL is `url`. Only then is its validity checked, so a
    closed posting is reported as closed rather than replaced by an open
    posting that happens to share its page.
    """
    listed = read_postings(markup, url)
    if not listed:
        raise _no_data(url)
    if len(listed) > 1:
        listed = [item for item in listed if _page_key(item.posting.url) == _page_key(url)]
        if len(listed) != 1:
            raise DiscoveryError(
                f"{url} lists several postings; resolve the URL of the one you want"
            )
    reason = listed[0].closed_reason(now)
    if reason is not None:
        raise DiscoveryError(f"the posting at {url} is not usable: {reason}")
    return listed[0].posting
