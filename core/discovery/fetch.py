"""Every network request discovery makes goes through `HttpFetcher`.

The rules are enforced here rather than left to callers:

- **Only documented public posting APIs**, over HTTPS: the hosts in
  `ALLOWED_HOSTS` -- three employer-published applicant-tracking APIs, and
  Algolia's public API for Hacker News, whose monthly "Who is hiring?"
  thread is companies posting their own openings. A request to any other host is refused before a
  connection is attempted.
- **Never a platform that forbids automated access.** `DENIED_DOMAINS`
  names them. They cannot be added to the allowlist by accident, because a
  URL under one is refused by name first.
- **Politely.** An honest User-Agent naming the project; at least
  `MIN_INTERVAL` seconds between requests to one host (Lever's robots.txt
  asks for a crawl delay of 1); a timeout; and a cap on response size.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset(
    {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com", "hn.algolia.com"}
)
DENIED_DOMAINS = frozenset(
    {"linkedin.com", "indeed.com", "joinhandshake.com", "myworkdayjobs.com", "glassdoor.com"}
)
USER_AGENT = "callback/0.1 (personal job search; +https://github.com/aidankaras/callback)"
MIN_INTERVAL = 1.0
TIMEOUT = 20.0
MAX_BYTES = 20 * 1024 * 1024


class DiscoveryError(RuntimeError):
    """A posting source that could not be read, or may not be."""


def denied_domain(host: str) -> str | None:
    """The forbidden domain `host` belongs to, if any."""
    host = host.lower().rstrip(".")
    return next((d for d in DENIED_DOMAINS if host == d or host.endswith("." + d)), None)


def check_url(url: str, allowed: frozenset[str] = ALLOWED_HOSTS) -> None:
    """Refuse `url` unless it is HTTPS to a host in `allowed`; name a denied platform."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    forbidden = denied_domain(host)
    if forbidden:
        raise DiscoveryError(f"{forbidden} does not allow automated access")
    if parts.scheme != "https" or host not in allowed:
        raise DiscoveryError(f"{url!r} is not a documented public posting API")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a URL the fetcher would request directly.

    urllib follows redirects by default, so without this an allowed host
    answering with a redirect would take a request anywhere, including a
    platform that forbids automated access.
    """

    def __init__(self, allowed: frozenset[str] = ALLOWED_HOSTS) -> None:
        self.allowed = allowed

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        check_url(newurl, self.allowed)
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


class Fetcher(Protocol):
    def get_json(self, url: str) -> object: ...


class HttpFetcher:
    """Fetches JSON from the sanctioned hosts, one polite request at a time.

    `allowed` defaults to the posting APIs; another caller names its own
    hosts, so adding one for it never widens what discovery can reach.
    """

    def __init__(self, allowed: frozenset[str] = ALLOWED_HOSTS) -> None:
        self._allowed = allowed
        self._last: dict[str, float] = {}
        self._opener = urllib.request.build_opener(_CheckedRedirects(allowed))

    def get_json(self, url: str) -> object:
        check_url(url, self._allowed)
        host = (urlsplit(url).hostname or "").lower()
        wait = MIN_INTERVAL - (time.monotonic() - self._last.get(host, float("-inf")))
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener.open(request, timeout=TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError) as exc:
            raise DiscoveryError(f"could not fetch {url}: {exc}") from exc
        finally:
            self._last[host] = time.monotonic()
        if len(body) > MAX_BYTES:
            raise DiscoveryError(f"{url} returned more than {MAX_BYTES} bytes")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"{url} did not return JSON: {exc}") from exc
