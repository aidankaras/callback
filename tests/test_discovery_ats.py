from __future__ import annotations

import pytest

from core.discovery.ats import html_to_text, parse_board, posting_url_for, resolve_url
from core.discovery.fetch import DiscoveryError, HttpFetcher

# Synthetic payloads in the shapes the three public APIs returned on 2026-09-22.
GREENHOUSE = {
    "jobs": [
        {
            "id": 111,
            "title": "Data Science Intern, Summer 2027",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/111",
            "location": {"name": "Boston, MA"},
            "updated_at": "2026-09-17T17:25:09-04:00",
            "company_name": "Acme Robotics",
            # Greenhouse returns HTML with its markup entity-escaped.
            "content": (
                "&lt;p&gt;Build &lt;strong&gt;models&lt;/strong&gt; in Python.&lt;/p&gt;"
                "&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;"
            ),
        }
    ]
}
LEVER = [
    {
        "id": "ac97-1",
        "text": "Machine Learning Intern",
        "hostedUrl": "https://jobs.lever.co/acme/ac97-1",
        "applyUrl": "https://jobs.lever.co/acme/ac97-1/apply",
        "createdAt": 1711403416463,
        "categories": {"location": "Remote, US", "commitment": "Intern"},
        "descriptionPlain": "Train models.",
        "additionalPlain": "Python required.",
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "8fb1",
            "title": "AI Research Intern",
            "location": "San Francisco",
            "jobUrl": "https://jobs.ashbyhq.com/acme/8fb1",
            "applyUrl": "https://jobs.ashbyhq.com/acme/8fb1/application",
            "publishedAt": "2026-03-12T16:38:15.322+00:00",
            "descriptionPlain": "Research on LLM agents.",
            "isListed": True,
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False, "jobUrl": "x", "applyUrl": "y"},
    ]
}


def test_greenhouse_postings_carry_the_employer_text_as_plain_text() -> None:
    (p,) = parse_board("greenhouse", "acme", "Acme Robotics", GREENHOUSE)
    assert p.id == "greenhouse:acme:111"
    assert p.title == "Data Science Intern, Summer 2027"
    assert p.location == "Boston, MA"
    assert p.url == "https://job-boards.greenhouse.io/acme/jobs/111"
    assert p.text == "Build models in Python.\nSQL"


def test_lever_postings_join_their_description_sections() -> None:
    (p,) = parse_board("lever", "acme", "Acme Robotics", LEVER)
    assert (p.id, p.location, p.apply_url) == (
        "lever:acme:ac97-1",
        "Remote, US",
        "https://jobs.lever.co/acme/ac97-1/apply",
    )
    assert p.text == "Train models.\n\nPython required."


def test_ashby_skips_unlisted_postings() -> None:
    postings = parse_board("ashby", "acme", "Acme Robotics", ASHBY)
    assert [p.id for p in postings] == ["ashby:acme:8fb1"]


def test_a_payload_in_an_unexpected_shape_is_an_error_not_an_empty_board() -> None:
    """An API that changes shape must not read as "no openings"."""
    with pytest.raises(DiscoveryError, match="greenhouse"):
        parse_board("greenhouse", "acme", "Acme", {"unexpected": []})
    with pytest.raises(DiscoveryError, match="lever"):
        parse_board("lever", "acme", "Acme", {"jobs": []})


def test_html_to_text_keeps_block_structure_and_drops_tags() -> None:
    assert html_to_text("<h2>Role</h2><p>One &amp; two</p><ul><li>A</li><li>B</li></ul>") == (
        "Role\nOne & two\nA\nB"
    )
    assert html_to_text("<script>alert(1)</script><p>Visible</p>") == "Visible"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://job-boards.greenhouse.io/anthropic/jobs/5421031008",
            ("greenhouse", "anthropic", "5421031008"),
        ),
        (
            "https://boards.greenhouse.io/anthropic/jobs/5421031008?gh_src=x",
            ("greenhouse", "anthropic", "5421031008"),
        ),
        (
            "https://jobs.lever.co/palantir/ac978161-6f46/apply",
            ("lever", "palantir", "ac978161-6f46"),
        ),
        ("https://jobs.ashbyhq.com/openai/8fb1615c/application", ("ashby", "openai", "8fb1615c")),
    ],
)
def test_a_posting_url_resolves_to_its_board_and_id(
    url: str, expected: tuple[str, str, str]
) -> None:
    assert resolve_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/123",
        "https://www.indeed.com/viewjob?jk=abc",
        "https://app.joinhandshake.com/jobs/123",
        "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/123",
    ],
)
def test_a_platform_that_forbids_automation_is_named_not_resolved(url: str) -> None:
    with pytest.raises(DiscoveryError, match="does not allow automated access"):
        resolve_url(url)


def test_board_api_urls_are_the_documented_public_endpoints() -> None:
    assert posting_url_for("greenhouse", "acme") == (
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    )
    assert posting_url_for("lever", "acme") == "https://api.lever.co/v0/postings/acme?mode=json"
    assert posting_url_for("ashby", "acme") == (
        "https://api.ashbyhq.com/posting-api/job-board/acme"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/voyager/api/jobs",
        "https://example.com/jobs.json",
        "http://api.lever.co/v0/postings/acme?mode=json",
        "https://api.lever.co.evil.test/v0/postings/acme",
    ],
)
def test_the_fetcher_only_reaches_the_sanctioned_hosts_over_https(url: str) -> None:
    """Refused before any connection is attempted."""
    with pytest.raises(DiscoveryError):
        HttpFetcher().get_json(url)


def test_a_board_name_cannot_smuggle_a_path() -> None:
    with pytest.raises(DiscoveryError, match="board"):
        posting_url_for("greenhouse", "acme/../../other")


def test_the_suite_cannot_reach_the_network() -> None:
    """The guard in conftest.py. Mutation: remove it -- this fails (or hangs)."""
    import socket

    with pytest.raises(RuntimeError, match="network"), socket.socket() as s:
        s.connect(("192.0.2.1", 443))  # TEST-NET-1: never routable


@pytest.mark.parametrize(
    "url", ["https://www.linkedin.com/voyager/api/jobs", "https://jobs.indeed.com/api/x"]
)
def test_the_fetcher_names_why_a_forbidden_platform_is_refused(url: str) -> None:
    """The allowlist would refuse these too; the denylist is what says why, and
    what still refuses them if one is ever added to the allowlist by mistake.
    Mutation: drop the denylist check -- the message assertion fails."""
    with pytest.raises(DiscoveryError, match="does not allow automated access"):
        HttpFetcher().get_json(url)


def test_a_line_break_element_without_a_closing_tag_still_breaks_the_line() -> None:
    assert html_to_text("one<br>two") == "one\ntwo"


# --- Hacker News "Who is hiring" -------------------------------------------------------

HN_SEARCH = {
    "hits": [
        {"objectID": "200", "title": "Ask HN: Who wants to be hired? (September 2026)"},
        {"objectID": "100", "title": "Ask HN: Who is hiring? (September 2026)"},
    ]
}
HN_THREAD = {
    "id": 100,
    "children": [
        {
            "id": 101,
            "text": "Acme AI | Machine Learning Intern | Boston, MA | ONSITE | "
            '<a href="https://acme.ai/jobs">https://acme.ai/jobs</a><p>We train models.',
            "created_at": "2026-09-01T15:01:54.000Z",
        },
        {"id": 102, "text": "Just a question about the thread format?"},
        {"id": 103, "text": None},
    ],
}


def test_hn_posts_follow_the_pipe_convention_and_others_are_skipped() -> None:
    from core.discovery.ats import parse_hn_thread

    (p,) = parse_hn_thread(HN_THREAD)
    assert (p.id, p.company, p.title, p.location) == (
        "hn:100:101",
        "Acme AI",
        "Machine Learning Intern",
        "Boston, MA",
    )
    assert p.url == "https://news.ycombinator.com/item?id=101"
    assert p.apply_url == "https://acme.ai/jobs"
    assert "We train models." in p.text


def test_the_latest_hiring_thread_is_chosen_not_the_seekers_thread() -> None:
    """Mutation: take the first hit -- that is "Who wants to be hired"."""
    from core.discovery.ats import latest_hn_thread

    assert latest_hn_thread(HN_SEARCH) == "100"
    with pytest.raises(DiscoveryError, match="hiring thread"):
        latest_hn_thread({"hits": []})


@pytest.mark.parametrize(
    "target",
    [
        "https://www.linkedin.com/jobs/view/1",
        "http://api.lever.co/v0/postings/acme",
        "https://example.com/elsewhere",
    ],
)
def test_a_redirect_is_followed_only_to_where_the_fetcher_may_go(target: str) -> None:
    """urllib follows redirects by default. Mutation: drop the redirect check
    -- a 301 from an allowed host would reach any of these."""
    import urllib.request

    from core.discovery.fetch import _CheckedRedirects  # pyright: ignore[reportPrivateUsage]

    request = urllib.request.Request("https://api.lever.co/v0/postings/acme?mode=json")
    with pytest.raises(DiscoveryError):
        _CheckedRedirects().redirect_request(request, None, 301, "Moved", {}, target)
    allowed = _CheckedRedirects().redirect_request(
        request, None, 301, "Moved", {}, "https://api.lever.co/v0/postings/acme2?mode=json"
    )
    assert allowed is not None
