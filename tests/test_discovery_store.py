from __future__ import annotations

import hashlib
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


# --- postings added by hand -----------------------------------------------------------

DAAD_URL = "https://www.example.org/rise/germany"


def test_a_manual_posting_is_stored_with_its_deadline() -> None:
    from core.discovery.store import add_manual

    deadline = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    found = add_manual(
        "Example Exchange", "Summer Research", DAAD_URL, "Ten weeks.", deadline, now=NOW
    )
    digest = hashlib.sha256(DAAD_URL.encode()).hexdigest()[:8]
    assert found.id == f"manual:example-exchange:{digest}"
    assert load_postings()[found.id].deadline == deadline


def test_adding_the_same_url_again_updates_it_in_place() -> None:
    """Mutation: mint the id from the time instead of the URL -- two postings."""
    from core.discovery.store import add_manual

    first = add_manual("Example Exchange", "Summer Research", DAAD_URL, "Old.", None, now=NOW)
    later = NOW + timedelta(days=3)
    deadline = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    again = add_manual("Example Exchange", "Summer Research", DAAD_URL, "New.", deadline, now=later)
    assert again.id == first.id
    (stored,) = load_postings().values()
    assert (stored.text, stored.deadline, stored.first_seen) == ("New.", deadline, NOW)


def test_a_fragment_is_one_page_and_another_query_is_another() -> None:
    """Mutations: keep the fragment -- two postings; drop the query -- one."""
    from core.discovery.store import add_manual

    first = add_manual("Example", "Research", DAAD_URL, "Old.", None, now=NOW)
    again = add_manual("Example", "Research", f"{DAAD_URL}#apply", "New.", None, now=NOW)
    assert again.id == first.id
    job = "https://boards.greenhouse.io/embed/job_app?gh_jid="
    one = add_manual("Acme", "Intern", f"{job}1", "One.", None, now=NOW)
    two = add_manual("Acme", "Intern", f"{job}2", "Two.", None, now=NOW)
    assert one.id != two.id
    assert len(load_postings()) == 3


def test_the_url_identifies_a_manual_posting_whatever_the_company_is_called() -> None:
    """Mutation: key the id on the company slug alone -- two postings."""
    from core.discovery.store import add_manual

    first = add_manual("Example", "Summer Research", DAAD_URL, "Old.", None, now=NOW)
    again = add_manual("Example Exchange e.V.", "Summer Research", DAAD_URL, "New.", None, now=NOW)
    assert again.id == first.id
    assert [p.text for p in load_postings().values()] == ["New."]


def test_a_watch_leaves_a_manual_posting_in_place() -> None:
    """No board lists it, so a watch that pruned unlisted postings would drop it."""
    from core.discovery.store import add_manual

    manual = add_manual(
        "Example Exchange", "Summer Research", DAAD_URL, "Ten weeks.", None, now=NOW
    )
    add_target(Target(company="Acme Robotics", ats="greenhouse", board="acme"))
    result = watch(FakeFetcher({"acme": _greenhouse("Data Science Intern")}), now=NOW)
    assert [p.title for p in result.new] == ["Data Science Intern"]  # premise: the watch wrote
    assert manual.id in load_postings()


@pytest.mark.parametrize(
    ("company", "url", "text"),
    [
        ("", DAAD_URL, "Ten weeks."),
        ("Example Exchange", DAAD_URL, "   "),
        ("Example Exchange", "javascript:alert(1)", "Ten weeks."),
        ("Example Exchange", "example.org/rise", "Ten weeks."),
    ],
)
def test_a_manual_posting_needs_a_company_its_text_and_a_web_url(
    company: str, url: str, text: str
) -> None:
    from core.discovery.store import add_manual

    with pytest.raises(DiscoveryError):
        add_manual(company, "Summer Research", url, text, None, now=NOW)
    assert load_postings() == {}


def test_a_deadline_must_carry_a_timezone() -> None:
    from pydantic import ValidationError

    from core.discovery.store import Found

    with pytest.raises(ValidationError, match="timezone"):
        Found(
            id="manual:x:00000000",
            ats="manual",
            board="x",
            company="X",
            title="T",
            url=DAAD_URL,
            text="t",
            deadline=datetime(2026, 11, 30, 23, 59),  # noqa: DTZ001 -- the naive value under test
            first_seen=NOW,
        )


@pytest.fixture
def new_york(monkeypatch: pytest.MonkeyPatch) -> object:
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield None
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("new_york")
def test_a_bare_date_is_the_end_of_that_day_in_that_day_s_local_offset() -> None:
    """30 November is after the clocks change: -05:00, where 20 October's offset
    is -04:00. Mutation: attach the offset in force when the test runs -- one of
    the two dates then gets the other's offset."""
    from core.discovery.store import parse_deadline

    assert parse_deadline("2026-11-30").isoformat() == "2026-11-30T23:59:00-05:00"
    assert parse_deadline("2026-10-20").isoformat() == "2026-10-20T23:59:00-04:00"
    assert parse_deadline("2026-11-30T17:00+01:00").isoformat() == "2026-11-30T17:00:00+01:00"
    with pytest.raises(DiscoveryError):
        parse_deadline("30/11/2026")


def test_the_postings_add_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from core.cli import main

    text = tmp_path / "rise.txt"
    text.write_text("A 10-week internship in a German lab.\n", encoding="utf-8")
    args = ["postings", "add", "--company", "Example Exchange", "--title", "Summer Research"]
    args += ["--text-file", str(text)]
    assert main([*args, "--url", DAAD_URL, "--deadline", "2026-11-30T23:59+01:00"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("manual:example-exchange:")
    assert "deadline 2026-11-30T23:59:00+01:00" in out
    assert main([*args, "--url", "nope"]) == 3
    assert main([*args, "--url", DAAD_URL, "--deadline", "30/11/2026"]) == 3
    assert "30/11/2026" in capsys.readouterr().err


@pytest.mark.usefixtures("new_york")
@pytest.mark.parametrize("value", ["20261130", "2026-W48-1", "2026-11-30T", "2026-11"])
def test_a_deadline_in_another_iso_shape_is_refused(value: str) -> None:
    """`20261130` and `2026-W48-1` are valid ISO and parse to midnight, a day
    early for an end-of-day deadline. Mutation: pass every non-date string to
    `fromisoformat` -- the first two are then accepted."""
    from core.discovery.store import parse_deadline

    with pytest.raises(DiscoveryError):
        parse_deadline(value)
    assert datetime.fromisoformat("20261130") is not None  # the premise: Python reads it
    assert parse_deadline("2026-11-30 17:00").isoformat() == "2026-11-30T17:00:00-05:00"


def test_one_page_in_five_spellings_is_one_posting() -> None:
    """Mutation: store and hash the URL as given -- five ids, not one."""
    from core.discovery.store import add_manual

    base = "https://www.example.org/rise/germany"
    spellings = [
        base,
        base + "/",
        base + "#apply",
        "HTTPS://www.example.org/rise/germany",
        "https://WWW.Example.ORG/rise/germany",
    ]
    assert len(set(spellings)) == 5  # the premise
    later = NOW + timedelta(days=1)
    first = add_manual("Example", "Summer Research", base, "Old.", None, now=NOW)
    for spelling in spellings[1:]:
        again = add_manual("Example", "Summer Research", spelling, "New.", None, now=later)
        assert again.id == first.id
        assert again.first_seen == NOW
        assert again.url == base
    assert len(load_postings()) == 1
    other = add_manual("Example", "Summer Research", base + "?year=2027", "x", None, now=NOW)
    assert other.id != first.id
    assert add_manual("E", "T", "https://x.org/", "x", None, now=NOW).url == "https://x.org/"


@pytest.mark.parametrize("url", ["https://[bad", "https://x.org:99999999"])
def test_a_malformed_url_is_a_discovery_error(url: str) -> None:
    from core.discovery.store import add_manual

    with pytest.raises(DiscoveryError):
        add_manual("Example", "Summer Research", url, "Text.", None, now=NOW)


def test_postings_add_refuses_a_text_file_that_is_not_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    text = tmp_path / "bad.txt"
    text.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(UnicodeDecodeError):
        text.read_text(encoding="utf-8")  # the premise
    args = ["postings", "add", "--company", "E", "--title", "T", "--url", DAAD_URL]
    assert main([*args, "--text-file", str(text)]) == 3
    assert "callback postings add" in capsys.readouterr().err


def test_a_re_add_keeps_the_deadline_and_location_unless_new_ones_are_given() -> None:
    """Mutation: store the deadline and location given, even when none is --
    the re-add erases both."""
    from core.discovery.store import add_manual

    deadline = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    add_manual("CERN", "Summer Student", DAAD_URL, "Old.", deadline, now=NOW, location="Geneva")
    again = add_manual("CERN", "Summer Student", DAAD_URL, "New.", None, now=NOW)
    assert (again.text, again.deadline, again.location) == ("New.", deadline, "Geneva")
    later = deadline + timedelta(days=30)
    moved = add_manual(
        "CERN", "Summer Student", DAAD_URL, "New.", later, now=NOW, location="Zurich"
    )
    assert (moved.deadline, moved.location) == (later, "Zurich")


def test_a_re_add_without_a_deadline_drops_one_that_has_passed() -> None:
    """Mutation: always keep the previous deadline -- a yearly programme's
    old deadline hides it from the agenda."""
    from core.discovery.store import add_manual

    old = NOW - timedelta(days=30)
    stored = add_manual(
        "CERN", "Summer Student", DAAD_URL, "Old.", old, now=NOW - timedelta(days=60)
    )
    assert stored.deadline is not None and stored.deadline < NOW
    again = add_manual("CERN", "Summer Student", DAAD_URL, "New.", None, now=NOW)
    assert again.deadline is None
    assert load_postings()[again.id].deadline is None


def test_a_re_add_without_a_deadline_keeps_one_still_ahead() -> None:
    from core.discovery.store import add_manual

    ahead = NOW + timedelta(days=30)
    add_manual("CERN", "Summer Student", DAAD_URL, "Old.", ahead, now=NOW)
    assert add_manual("CERN", "Summer Student", DAAD_URL, "New.", None, now=NOW).deadline == ahead


def test_a_hand_edited_stored_url_still_identifies_the_posting() -> None:
    """A stored URL edited by hand into another spelling of the same page.
    Mutation: compare the stored URL as written -- `first_seen` is reset."""
    import json

    from core.discovery.store import add_manual
    from core.paths import resolve_inside

    first = add_manual("Acme", "Research Intern", "https://acme.org/jobs/1", "Old.", None, now=NOW)
    path = resolve_inside("postings.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[first.id]["url"] = "HTTPS://Acme.org/jobs/1/"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_postings()[first.id].url != first.url  # premise: the edit is stored
    again = add_manual(
        "Acme",
        "Research Intern",
        "https://acme.org/jobs/1",
        "New.",
        None,
        now=NOW + timedelta(days=2),
    )
    assert (again.id, again.first_seen) == (first.id, NOW)
    assert len(load_postings()) == 1


def test_a_city_tier_reads_a_hand_added_posting_s_location() -> None:
    """EPFL and CERN name their city only in their text. Mutation: drop
    `location` from the stored posting -- both fall to `other`, below Chicago."""
    from core.discovery.store import Found, add_manual
    from core.triage import Profile, Tier, rank

    europe = Tier(name="europe", criteria=Criteria(include=["geneva", "lausanne"]))
    us = Tier(name="us", criteria=Criteria(include=["chicago"]))
    profile = Profile(tiers=(europe, us))
    cern = add_manual(
        "CERN",
        "Summer Student",
        "https://cern.ch/ssp",
        "Ten weeks.",
        None,
        now=NOW,
        location="Geneva",
    )
    epfl = add_manual(
        "EPFL", "Summer@EPFL", "https://epfl.ch/s", "Ten weeks.", None, now=NOW, location="Lausanne"
    )
    bare = add_manual(
        "CERN", "Summer Student", "https://cern.ch/other", "Ten weeks in Geneva.", None, now=NOW
    )
    chicago = Found(
        id="greenhouse:c:1",
        ats="greenhouse",
        board="c",
        company="Chi",
        title="Analyst",
        location="Chicago, IL",
        url="https://c.com/1",
        text="Ten weeks.",
        first_seen=NOW,
    )
    assert rank([bare], profile, {}, now=NOW)[0].tier == "other"  # premise: the text is not read
    ranked = rank([chicago, cern, epfl], profile, {}, now=NOW)
    assert [(r.posting.company, r.tier) for r in ranked] == [
        ("CERN", "europe"),
        ("EPFL", "europe"),
        ("Chi", "us"),
    ]


def test_postings_add_warns_of_a_deadline_already_past(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wrong year stores a posting the agenda drops a week later. Mutation:
    drop the warning -- stderr is empty."""
    from core.cli import main

    text = tmp_path / "rise.txt"
    text.write_text("A 10-week internship.\n", encoding="utf-8")
    args = ["postings", "add", "--company", "CERN", "--title", "Summer Student"]
    args += ["--text-file", str(text), "--url", "https://cern.ch/ssp", "--location", "Geneva"]
    assert main([*args, "--deadline", "2099-10-20"]) == 0
    assert "warning" not in capsys.readouterr().err  # premise: a future deadline is quiet
    assert main([*args, "--deadline", "2025-10-20"]) == 0
    assert "has already passed" in capsys.readouterr().err
    (stored,) = load_postings().values()
    assert (
        stored.deadline is not None and stored.deadline.year == 2025 and stored.location == "Geneva"
    )
