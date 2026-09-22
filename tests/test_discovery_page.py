from __future__ import annotations

import email.message
import io
import json
import urllib.request
import urllib.response
from datetime import UTC, datetime
from pathlib import Path

import pytest

import core.discovery.page
from core.discovery.fetch import DiscoveryError
from core.discovery.page import PageFetcher, parse_job_postings
from core.discovery.store import load_postings, resolve
from core.paths import ensure_home

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
PAGE_URL = "https://careers.acme.example/jobs/ml-intern"


def _page(*blocks: object, raw: str = "") -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    )
    return f"<html><head>{scripts}{raw}</head><body><h1>Careers</h1></body></html>"


def _posting(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Machine Learning Intern",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Robotics"},
        "description": "<p>Build <b>perception</b> models.</p><ul><li>Python</li></ul>",
        "datePosted": "2026-09-20",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "Boston",
                "addressRegion": "MA",
                "addressCountry": "US",
            },
        },
        "identifier": {"@type": "PropertyValue", "value": "R-1042"},
        "url": PAGE_URL,
    }
    body.update(overrides)
    return body


# --- reading the structured data ------------------------------------------------------


def test_a_job_posting_block_becomes_a_posting() -> None:
    (p,) = parse_job_postings(_page(_posting()), PAGE_URL, now=NOW)
    assert (p.id, p.ats, p.company, p.title) == (
        "page:careers.acme.example:R-1042",
        "page",
        "Acme Robotics",
        "Machine Learning Intern",
    )
    assert p.location == "Boston, MA, US"
    assert p.text == "Build perception models.\nPython"
    assert p.published_at == datetime(2026, 9, 20, tzinfo=UTC)


def test_postings_are_found_inside_a_graph_and_beside_bad_blocks() -> None:
    """A page often carries other structured data, some of it broken.
    Mutation: stop at the first unreadable block -- the posting is lost."""
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, _posting()]}
    markup = _page(graph, raw='<script type="application/ld+json">{not json</script>')
    markup = markup.replace("<head>", '<head><script type="application/ld+json">{oops</script>')
    assert [p.title for p in parse_job_postings(markup, PAGE_URL, now=NOW)] == [
        "Machine Learning Intern"
    ]


def test_a_remote_posting_says_so() -> None:
    (p,) = parse_job_postings(_page(_posting(jobLocationType="TELECOMMUTE")), PAGE_URL, now=NOW)
    assert p.location == "Remote"


def test_a_closed_posting_is_refused_with_its_date() -> None:
    """The employer's own validThrough is the plainest ghost-job signal.
    Mutation: ignore validThrough -- the closed posting is stored."""
    closed = _posting(validThrough="2026-09-01T00:00:00Z")
    with pytest.raises(DiscoveryError, match=r"closed 2026-09-01"):
        parse_job_postings(_page(closed), PAGE_URL, now=NOW)
    # Premise: the same posting, still open, is read.
    assert parse_job_postings(_page(_posting(validThrough="2026-12-01")), PAGE_URL, now=NOW)


def test_a_page_without_structured_data_says_to_paste_instead() -> None:
    with pytest.raises(DiscoveryError, match="paste"):
        parse_job_postings("<html><body>Jobs</body></html>", PAGE_URL, now=NOW)


@pytest.mark.parametrize("missing", ["title", "hiringOrganization", "description"])
def test_a_posting_missing_what_a_resume_is_tailored_to_is_skipped(missing: str) -> None:
    body = _posting()
    del body[missing]
    with pytest.raises(DiscoveryError, match=r"no schema\.org JobPosting"):
        parse_job_postings(_page(body), PAGE_URL, now=NOW)


# --- the fetcher's rules ----------------------------------------------------------------

Route = tuple[int, str, bytes, str | None]  # status, content type, body, Location


class _Site(urllib.request.BaseHandler):
    """Answers HTTPS itself -- no socket -- from `routes`, recording each URL."""

    handler_order = 100

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.opened: list[str] = []

    def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        self.opened.append(req.full_url)
        status, ctype, body, location = self.routes.get(req.full_url, (404, "", b"", None))
        headers = email.message.Message()
        if ctype:
            headers["Content-Type"] = ctype
        if location:
            headers["Location"] = location
        response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, status)
        response.msg = "OK" if status == 200 else "Other"  # type: ignore[attr-defined]
        return response


ROBOTS = "https://careers.acme.example/robots.txt"
HTML = "text/html; charset=utf-8"


def _fetcher(
    routes: dict[str, Route], addresses: list[str] | None = None
) -> tuple[PageFetcher, _Site]:
    site = _Site(routes)
    fetcher = PageFetcher(resolver=lambda host: addresses or ["93.184.216.34"])
    fetcher._extra.append(site)  # type: ignore[reportPrivateUsage]
    return fetcher, site


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core.discovery.page, "MIN_INTERVAL", 0.0)


def test_a_permitted_page_is_fetched_after_its_robots_file() -> None:
    fetcher, site = _fetcher(
        {
            ROBOTS: (200, "text/plain", b"User-agent: *\nAllow: /\n", None),
            PAGE_URL: (200, HTML, b"<html>ok</html>", None),
        }
    )
    assert fetcher.get_page(PAGE_URL) == "<html>ok</html>"
    assert site.opened == [ROBOTS, PAGE_URL]


def test_robots_disallow_is_honoured() -> None:
    """Mutation: skip the robots check -- the page is fetched."""
    fetcher, site = _fetcher(
        {
            ROBOTS: (200, "text/plain", b"User-agent: *\nDisallow: /jobs/\n", None),
            PAGE_URL: (200, HTML, b"x", None),
        }
    )
    with pytest.raises(DiscoveryError, match=r"robots\.txt does not allow"):
        fetcher.get_page(PAGE_URL)
    assert site.opened == [ROBOTS]


@pytest.mark.parametrize(
    ("status", "allowed"), [(404, True), (403, True), (500, False), (503, False)]
)
def test_a_robots_file_that_cannot_be_read_refuses_unless_it_is_missing(
    status: int, allowed: bool
) -> None:
    """A 4xx means there are no rules; anything else means they are unknown."""
    fetcher, _ = _fetcher({ROBOTS: (status, "", b"", None), PAGE_URL: (200, HTML, b"ok", None)})
    if allowed:
        assert fetcher.get_page(PAGE_URL) == "ok"
    else:
        with pytest.raises(DiscoveryError, match="could not be read"):
            fetcher.get_page(PAGE_URL)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "::1", "fd00::1"]
)
def test_a_host_on_a_private_network_is_refused_before_any_request(address: str) -> None:
    """Mutation: skip the address check -- the request is made."""
    fetcher, site = _fetcher({}, addresses=["93.184.216.34", address])
    with pytest.raises(DiscoveryError, match="non-public address"):
        fetcher.get_page(PAGE_URL)
    assert site.opened == []


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://www.linkedin.com/jobs/view/1", "linkedin.com does not allow"),
        ("https://acme.myworkdayjobs.com/en-US/careers/job/1", "myworkdayjobs.com does not allow"),
        ("http://careers.acme.example/jobs/ml-intern", "not an https URL"),
    ],
)
def test_a_denied_platform_or_plain_http_is_refused(url: str, reason: str) -> None:
    fetcher, site = _fetcher({})
    with pytest.raises(DiscoveryError, match=reason):
        fetcher.get_page(url)
    assert site.opened == []


def test_a_redirect_is_followed_only_on_the_same_host() -> None:
    """Mutation: follow any redirect -- the request reaches another host."""
    moved = "https://careers.acme.example/jobs/1042"
    off_host = "https://tracker.example.net/collect?u=1"
    robots = (200, "text/plain", b"User-agent: *\nAllow: /\n", None)
    fetcher, _ = _fetcher(
        {ROBOTS: robots, PAGE_URL: (301, "", b"", moved), moved: (200, HTML, b"moved", None)}
    )
    assert fetcher.get_page(PAGE_URL) == "moved"
    fetcher, site = _fetcher({ROBOTS: robots, PAGE_URL: (302, "", b"", off_host)})
    with pytest.raises(DiscoveryError, match="redirects off its host"):
        fetcher.get_page(PAGE_URL)
    assert off_host not in site.opened


def test_a_response_that_is_not_html_is_refused() -> None:
    fetcher, _ = _fetcher(
        {ROBOTS: (404, "", b"", None), PAGE_URL: (200, "application/pdf", b"%PDF", None)}
    )
    with pytest.raises(DiscoveryError, match="not an HTML page"):
        fetcher.get_page(PAGE_URL)


# --- resolve routes by URL ----------------------------------------------------------------


class _Pages:
    def __init__(self, markup: str) -> None:
        self.markup = markup
        self.asked: list[str] = []

    def get_page(self, url: str) -> str:
        self.asked.append(url)
        return self.markup


class _NoApi:
    def get_json(self, url: str) -> object:
        raise AssertionError(f"the API fetcher was asked for {url}")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def test_a_careers_page_url_is_resolved_from_its_structured_data(home: Path) -> None:
    pages = _Pages(_page(_posting()))
    found = resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=pages)
    assert found.id == "page:careers.acme.example:R-1042"
    assert found.id in load_postings()
    assert pages.asked == [PAGE_URL]


def test_the_page_must_name_the_company_the_candidate_meant(home: Path) -> None:
    """Mutation: skip the employer check -- another company's posting is stored."""
    pages = _Pages(_page(_posting(hiringOrganization={"name": "Globex Staffing"})))
    with pytest.raises(DiscoveryError, match="names 'Globex Staffing'"):
        resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=pages)
    assert load_postings() == {}
    # Premise: a shorter form of the same name is accepted.
    ok = _Pages(_page(_posting(hiringOrganization={"name": "Acme Robotics, Inc."})))
    assert (
        resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=ok).company == "Acme Robotics"
    )


def test_a_listing_page_needs_the_url_of_one_posting(home: Path) -> None:
    other = _posting(
        title="Data Intern", url="https://careers.acme.example/jobs/data", identifier="R-7"
    )
    listing = _Pages(_page(_posting(), other))
    found = resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=listing)
    assert found.title == "Machine Learning Intern"
    with pytest.raises(DiscoveryError, match="several postings"):
        resolve(
            "https://careers.acme.example/jobs", "Acme Robotics", _NoApi(), now=NOW, pages=listing
        )


def test_a_denied_platform_is_refused_by_name_even_with_a_page_fetcher(home: Path) -> None:
    pages = _Pages(_page(_posting()))
    with pytest.raises(DiscoveryError, match=r"linkedin\.com does not allow"):
        resolve("https://www.linkedin.com/jobs/view/1", "Acme", _NoApi(), now=NOW, pages=pages)
    assert pages.asked == []
