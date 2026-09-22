from __future__ import annotations

from pathlib import Path

import pytest

from core.discovery.fetch import ALLOWED_HOSTS, DiscoveryError, HttpFetcher, check_url
from core.profile.github import GITHUB_API, draft_facts, repos_url
from core.profile.models import FactKind, Trust

# The shape of GET /users/<user>/repos, probed 2026-09-22 (fields trimmed).
REPOS: list[dict[str, object]] = [
    {
        "name": "signal-lab",
        "full_name": "dvex/signal-lab",
        "description": "Event-study pipeline over  public filings,\nwith point-in-time labels.",
        "language": "Python",
        "topics": ["python", "postgresql", "event-study", "sec-edgar"],
        "fork": False,
        "archived": False,
        "created_at": "2026-03-11T20:37:39Z",
        "pushed_at": "2026-09-20T01:08:23Z",
    },
    {
        "name": "someone-elses",
        "full_name": "dvex/someone-elses",
        "description": "A fork",
        "language": "Go",
        "topics": [],
        "fork": True,
        "created_at": "2025-01-01T00:00:00Z",
    },
    {
        "name": "dotfiles",
        "full_name": "dvex/dotfiles",
        "description": None,
        "language": "Shell",
        "topics": [],
        "fork": False,
        "created_at": "2024-05-01T00:00:00Z",
    },
]


def test_each_own_described_repository_becomes_an_unconfirmed_project_fact() -> None:
    drafted = draft_facts("dvex", REPOS)
    (fact,) = drafted.facts
    assert fact.id == "gh-signal-lab"
    assert fact.kind is FactKind.PROJECT
    assert fact.text == (
        "signal-lab: Event-study pipeline over public filings, with point-in-time labels."
    )
    # Topics are tags, not evidence: "postgresql" is one here and is not an entity.
    assert fact.entities == ["signal-lab", "Python"]
    assert fact.dates is not None
    assert (fact.dates.start, fact.dates.end) == ("2026-03", "2026-09")
    assert fact.provenance.trust is Trust.IMPORT_UNCONFIRMED
    assert fact.provenance.source == "github:dvex/signal-lab"
    assert not fact.selectable()


def test_forks_and_undescribed_repositories_are_skipped_with_a_reason() -> None:
    """Mutation: keep forks -- someone else's project becomes a fact."""
    skipped = dict(draft_facts("dvex", REPOS).skipped)
    assert skipped == {
        "someone-elses": "a fork is someone else's project",
        "dotfiles": "no description to draft a fact from",
    }


@pytest.mark.parametrize("user", ["../orgs", "a b", "-lead", "x" * 40, ""])
def test_a_username_that_is_not_one_is_refused(user: str) -> None:
    with pytest.raises(DiscoveryError, match="not a GitHub username"):
        repos_url(user)


def test_the_github_fetcher_reaches_only_github_and_discovery_never_does() -> None:
    """Each allowlist is its own: GitHub is not a posting API."""
    assert GITHUB_API not in ALLOWED_HOSTS
    with pytest.raises(DiscoveryError):
        check_url(repos_url("dvex"))
    check_url(repos_url("dvex"), frozenset({GITHUB_API}))
    with pytest.raises(DiscoveryError):
        HttpFetcher(frozenset({GITHUB_API})).get_json(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
        )


def test_the_command_writes_drafts_that_add_as_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The drafts go to a file, never the store, and survive a round trip
    through `facts add` still needing the candidate's confirmation."""
    import core.cli
    from core.cli import main
    from core.profile.store import load_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))

    class _Fake:
        def get_json(self, url: str) -> object:
            assert url == repos_url("dvex")
            return REPOS

    monkeypatch.setattr(core.cli, "_github_fetcher", _Fake)
    assert main(["facts", "github", "dvex"]) == 0
    out = tmp_path / "cb" / "inbox" / "github-dvex.yaml"
    assert "skipped dotfiles" in capsys.readouterr().out
    assert load_facts() == {}
    assert main(["facts", "add", str(out)]) == 0
    stored = load_facts()
    assert list(stored) == ["gh-signal-lab"]
    assert not stored["gh-signal-lab"].selectable()
