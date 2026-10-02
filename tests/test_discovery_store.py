from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.discovery.fetch import DiscoveryError
from core.discovery.store import (
    Criteria,
    Target,
    add_target,
    load_postings,
    load_targets,
    resolve,
    save_criteria,
    take,
    watch,
)
from core.paths import ensure_home

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _greenhouse(*titles: str) -> dict[str, object]:
    return {
        "jobs": [
            {
                "id": n,
                "title": t,
                "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{n}",
                "location": {"name": "Boston, MA"},
                "content": "&lt;p&gt;Train models in Python.&lt;/p&gt;",
            }
            for n, t in enumerate(titles, 1)
        ]
    }


class FakeFetcher:
    def __init__(self, boards: dict[str, object]) -> None:
        self.boards = boards
        self.requested: list[str] = []

    def get_json(self, url: str) -> object:
        self.requested.append(url)
        for board, payload in self.boards.items():
            if f"/{board}" in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise DiscoveryError(f"no such board in fake: {url}")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_criteria(
        Criteria(include=["data scien*", "machine learning", "intern"], exclude=["senior"])
    )
    return home


def test_a_target_is_added_once() -> None:
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    assert [t.board for t in load_targets()] == ["acme"]


def test_watch_stores_only_matching_postings_and_reports_them_once() -> None:
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    fetcher = FakeFetcher(
        {
            "acme": _greenhouse(
                "Data Science Intern", "Senior Machine Learning Engineer", "Office Manager"
            )
        }
    )
    first = watch(fetcher, now=NOW)
    assert [p.title for p in first.new] == ["Data Science Intern"]
    assert first.errors == []
    second = watch(fetcher, now=NOW + timedelta(hours=6))
    assert second.new == []
    (stored,) = load_postings().values()
    assert stored.first_seen == NOW


def test_one_failing_board_does_not_cost_the_others() -> None:
    add_target(Target(company="Broken Co", ats="greenhouse", board="broken"))
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    fetcher = FakeFetcher({"broken": DiscoveryError("503"), "acme": _greenhouse("ML Intern")})
    result = watch(fetcher, now=NOW)
    assert [p.title for p in result.new] == ["ML Intern"]
    assert [(t.board, "503" in msg) for t, msg in result.errors] == [("broken", True)]


def test_watch_without_criteria_is_refused_rather_than_matching_everything(home: Path) -> None:
    (home / "criteria.yaml").unlink()
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    with pytest.raises(DiscoveryError, match="criteria"):
        watch(FakeFetcher({"acme": _greenhouse("Anything")}), now=NOW)


def test_matching_is_by_whole_words_and_casefold() -> None:
    criteria = Criteria(include=["ml", "data analyst", "intern"], exclude=["director"])
    assert criteria.matches("Junior Data Analyst")
    assert criteria.matches("ML Research Intern")
    assert not criteria.matches("HTML Developer")
    assert not criteria.matches("Director of ML")
    # Measured on a live board: "intern" as a prefix matched this title.
    assert not criteria.matches("Strategic Finance, International & Marketing")


def test_a_trailing_star_makes_a_term_a_prefix() -> None:
    """Mutation: treat every term as a prefix -- "International" matches again."""
    criteria = Criteria(include=["intern*", "data scien*"])
    assert criteria.matches("Data Science Intern")
    assert criteria.matches("Summer Internship, Analytics")
    assert criteria.matches("Data Scientist")


def test_a_prefix_matches_every_word_it_begins() -> None:
    """The documented trap: "intern*" matches "International"."""
    assert Criteria(include=["intern*"]).matches("Engineering Manager, International")
    assert not Criteria(include=["intern", "internship"]).matches(
        "Engineering Manager, International"
    )


def test_require_is_an_and_over_include() -> None:
    """intern AND (data OR ML). Mutation: ignore `require` -- the first
    refusal fails; treat an empty `require` as matching nothing -- the last
    assertion fails."""
    criteria = Criteria(include=["intern", "internship"], require=["data", "ml"])
    assert criteria.matches("Data Science Intern")
    assert criteria.matches("ML Engineering Internship")
    assert not criteria.matches("Software Engineering Intern")
    assert not criteria.matches("Senior Data Engineer")
    assert Criteria(include=["intern"]).matches("Software Engineering Intern")


def test_a_posting_url_resolves_to_the_canonical_posting(home: Path) -> None:
    fetcher = FakeFetcher({"acme": _greenhouse("Office Manager", "Data Science Intern")})
    posting = resolve(
        "https://boards.greenhouse.io/acme/jobs/2?gh_src=x", "Acme Robotics", fetcher, now=NOW
    )
    assert posting.title == "Data Science Intern"
    assert posting.id in load_postings()
    with pytest.raises(DiscoveryError, match="not on"):
        resolve("https://boards.greenhouse.io/acme/jobs/99", "Acme Robotics", fetcher, now=NOW)


def test_taking_a_posting_saves_the_employer_text_with_the_heading_letters_need(home: Path) -> None:
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    (posting,) = watch(FakeFetcher({"acme": _greenhouse("Data Science Intern")}), now=NOW).new
    path = take(posting.id, "acme-ds-intern")
    text = path.read_text(encoding="utf-8")
    assert path == home.resolve() / "inbox" / "acme-ds-intern.md"
    assert text.splitlines()[0] == "# Data Science Intern - Acme Robotics"
    assert "https://job-boards.greenhouse.io/acme/jobs/1" in text
    assert "Train models in Python." in text
    with pytest.raises(DiscoveryError, match="no posting"):
        take("greenhouse:acme:404", "x")


def test_resolving_a_posting_already_seen_keeps_when_it_was_first_seen() -> None:
    """First-seen is how fresh a posting is. Mutation: overwrite it on resolve."""
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    fetcher = FakeFetcher({"acme": _greenhouse("Data Science Intern")})
    watch(fetcher, now=NOW)
    later = resolve(
        "https://job-boards.greenhouse.io/acme/jobs/1",
        "Acme Robotics",
        fetcher,
        now=NOW + timedelta(days=5),
    )
    assert later.first_seen == NOW


def test_the_discovery_commands_end_to_end(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import core.cli
    from core.cli import main

    fetcher = FakeFetcher(
        {"acme": _greenhouse("Data Science Intern"), "broken": DiscoveryError("503")}
    )
    monkeypatch.setattr(core.cli, "_fetcher", lambda: fetcher)
    assert main(["targets", "add", "Acme Robotics", "--ats", "greenhouse", "--board", "acme"]) == 0
    assert main(["criteria", "--include", "data scien*,intern", "--exclude", "senior"]) == 0
    assert main(["watch"]) == 0
    assert "NEW  greenhouse:acme:1" in capsys.readouterr().out
    assert main(["targets", "add", "Broken Co", "--ats", "greenhouse", "--board", "broken"]) == 0
    assert main(["watch"]) == 1, "a board that could not be read must not exit clean"
    assert "could not read Broken Co" in capsys.readouterr().err
    assert main(["postings", "list"]) == 0
    assert "Data Science Intern" in capsys.readouterr().out
    assert main(["postings", "take", "greenhouse:acme:1", "--job", "acme-ds"]) == 0
    assert (home / "inbox" / "acme-ds.md").read_text().startswith("# Data Science Intern - Acme")
    assert (
        main(["postings", "resolve", "https://www.linkedin.com/jobs/view/1", "--company", "X"]) == 3
    )
    assert "does not allow automated access" in capsys.readouterr().err
    assert main(["criteria", "--include", "intern", "--require", "data, ml"]) == 0
    from core.discovery.store import load_criteria

    assert load_criteria().require == ["data", "ml"]
