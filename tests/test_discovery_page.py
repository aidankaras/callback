from __future__ import annotations

import email.message
import io
import json
import urllib.error
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
    assert p.id.startswith("page:careers.acme.example:R-1042-")
    assert (p.ats, p.company, p.title) == (
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
    assert found.id.startswith("page:careers.acme.example:R-1042-")
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
    stored = resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=ok)
    assert stored.company == "Acme Robotics, Inc."  # the page's own name for itself


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


# --- the careful cases ------------------------------------------------------------------


def _robots(rules: str) -> Route:
    return (200, "text/plain", rules.encode(), None)


def test_a_same_host_redirect_into_a_disallowed_path_is_refused() -> None:
    """Mutation: check only the redirect's host -- the disallowed page is fetched."""
    private = "https://careers.acme.example/private/admin"
    fetcher, site = _fetcher(
        {
            ROBOTS: _robots("User-agent: *\nDisallow: /private/\n"),
            PAGE_URL: (302, "", b"", private),
            private: (200, HTML, b"secret", None),
        }
    )
    with pytest.raises(DiscoveryError, match=r"which robots\.txt disallows"):
        fetcher.get_page(PAGE_URL)
    assert private not in site.opened


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://careers.acme.example:8443/jobs/1", "names port 8443"),
        ("https://user:pw@careers.acme.example/jobs/1", "user name or password"),
        ("https://acme.example:abc/jobs/1", "is not a URL"),
        ("https://[::1/jobs", "is not a URL"),
    ],
)
def test_a_url_with_a_port_userinfo_or_bad_syntax_is_refused(url: str, reason: str) -> None:
    fetcher, site = _fetcher({})
    with pytest.raises(DiscoveryError, match=reason):
        fetcher.get_page(url)
    assert site.opened == []


def test_a_redirect_to_another_port_is_refused() -> None:
    fetcher, _ = _fetcher(
        {
            ROBOTS: _robots("User-agent: *\nAllow: /\n"),
            PAGE_URL: (302, "", b"", "https://careers.acme.example:8443/x"),
        }
    )
    with pytest.raises(DiscoveryError, match="port 8443"):
        fetcher.get_page(PAGE_URL)


@pytest.mark.parametrize("address", ["::127.0.0.1", "64:ff9b::a9fe:a9fe"])
def test_an_ipv6_address_embedding_ipv4_is_refused(address: str) -> None:
    """Both pass ipaddress's is_global. Mutation: drop the embedded check."""
    fetcher, _ = _fetcher({}, addresses=[address])
    with pytest.raises(DiscoveryError, match="non-public address"):
        fetcher.get_page(PAGE_URL)


def test_a_robots_file_that_failed_to_load_is_not_remembered_as_absent() -> None:
    """The first fetch fails in transit; the second must read robots again,
    not treat the failed read as "no rules". Mutation: keep the placeholder."""

    class _Flaky(urllib.request.BaseHandler):
        handler_order = 50
        failed = False

        def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl | None:
            if req.full_url == ROBOTS and not _Flaky.failed:
                _Flaky.failed = True
                raise urllib.error.URLError("connection reset")
            return None  # fall through to the next handler

    fetcher, site = _fetcher(
        {ROBOTS: _robots("User-agent: *\nDisallow: /\n"), PAGE_URL: (200, HTML, b"x", None)}
    )
    fetcher._extra.insert(0, _Flaky())  # type: ignore[reportPrivateUsage]
    with pytest.raises(DiscoveryError, match="connection reset"):
        fetcher.get_page(PAGE_URL)
    with pytest.raises(DiscoveryError, match=r"robots\.txt does not allow"):
        fetcher.get_page(PAGE_URL)
    assert PAGE_URL not in site.opened


def test_a_malformed_response_is_a_discovery_error() -> None:
    import http.client

    class _Broken(urllib.request.BaseHandler):
        handler_order = 50

        def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
            raise http.client.BadStatusLine("HTTP/1.1 abc OK")

    fetcher, _ = _fetcher({})
    fetcher._extra.insert(0, _Broken())  # type: ignore[reportPrivateUsage]
    with pytest.raises(DiscoveryError, match="could not fetch"):
        fetcher.get_page(PAGE_URL)


@pytest.mark.parametrize(
    ("wanted", "stated", "same"),
    [
        ("Acme", "Acme Robotics, Inc.", True),
        ("Acme Robotics", "ACME robotics LLC", True),
        ("Globex Staffing", "A", False),
        ("Acme Robotics, Inc.", "Inc.", False),
        ("Meta", "Metabase", False),
        ("Acme", "Acmeville Logistics", False),
        ("A", "Globex Staffing Agency", False),
    ],
)
def test_the_employer_must_be_the_same_name_in_whole_words(
    wanted: str, stated: str, same: bool
) -> None:
    """Mutation: compare as raw substrings -- the five false cases pass."""
    from core.discovery.store import same_company

    assert same_company(wanted, stated) is same


def test_a_closed_posting_is_not_replaced_by_its_open_neighbour(home: Path) -> None:
    """Mutation: drop closed postings before choosing by URL -- the Staff
    Engineer posting is stored in place of the closed internship."""
    closed = _posting(validThrough="2026-09-01")
    neighbour = _posting(
        title="Senior Staff Engineer",
        url="https://careers.acme.example/jobs/staff",
        identifier="R-7",
    )
    pages = _Pages(_page(closed, neighbour))
    with pytest.raises(DiscoveryError, match="closed 2026-09-01"):
        resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=pages)
    assert load_postings() == {}


@pytest.mark.parametrize(
    "stated", ["https://www.linkedin.com/jobs/view/999", "https://elsewhere.example/jobs/1"]
)
def test_a_posting_url_off_the_page_host_is_not_kept(home: Path, stated: str) -> None:
    pages = _Pages(_page(_posting(url=stated)))
    found = resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=pages)
    assert (found.url, found.apply_url) == (PAGE_URL, PAGE_URL)


def test_a_reused_identifier_does_not_merge_two_postings(home: Path) -> None:
    """Mutation: key by identifier alone -- resolving B returns A."""
    shared = {"@type": "PropertyValue", "name": "Acme", "value": "Acme"}
    a_url, b_url = "https://careers.acme.example/jobs/a", "https://careers.acme.example/jobs/b"
    a_page = _Pages(_page(_posting(url=a_url, identifier=shared)))
    b_page = _Pages(_page(_posting(url=b_url, identifier=shared, title="Data Engineer")))
    a = resolve(a_url, "Acme", _NoApi(), now=NOW, pages=a_page)
    b = resolve(b_url, "Acme", _NoApi(), now=NOW, pages=b_page)
    assert a.id != b.id
    assert b.title == "Data Engineer"


@pytest.mark.parametrize(
    "value", ["September 1, 2026", "2026/09/01", 1756684800, ["2026-09-01"], "2026-09-01 UTC"]
)
def test_an_unreadable_valid_through_is_not_read_as_open(value: object) -> None:
    """Mutation: treat an unreadable date as absent -- each posting is kept."""
    from core.discovery.page import posting_at

    with pytest.raises(DiscoveryError, match="cannot be read"):
        posting_at(_page(_posting(validThrough=value)), PAGE_URL, now=NOW)


def test_a_date_only_valid_through_is_open_all_that_day() -> None:
    """NOW is 15:00 on 1 October. Mutation: read the date as midnight -- closed."""
    from core.discovery.page import posting_at

    assert posting_at(_page(_posting(validThrough="2026-10-01")), PAGE_URL, now=NOW)


def test_an_undecodable_block_does_not_cost_the_posting_beside_it() -> None:
    """Mutation: catch only JSONDecodeError -- RecursionError escapes."""
    deep = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    big = '<script type="application/ld+json">{"n": ' + "9" * 5000 + "}</script>"
    markup = _page(_posting(), raw=deep + big)
    assert [p.title for p in parse_job_postings(markup, PAGE_URL, now=NOW)] == [
        "Machine Learning Intern"
    ]


def test_a_lone_surrogate_is_replaced_so_the_posting_can_be_written(home: Path) -> None:
    """Mutation: skip the cleaning -- `take` cannot encode the title."""
    from core.discovery.store import take

    # JSON's own escape for a lone high surrogate, which json.loads accepts.
    markup = _page(_posting()).replace("Machine Learning Intern", "Intern \\ud800")
    assert chr(0xD800) in json.loads(markup.split(">", 3)[3].split("</script>")[0])["title"]
    found = resolve(PAGE_URL, "Acme Robotics", _NoApi(), now=NOW, pages=_Pages(markup))
    assert chr(0xD800) not in found.title
    take(found.id, "acme-ml").read_text(encoding="utf-8")
