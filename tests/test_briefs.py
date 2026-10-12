from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from pydantic import ValidationError

from core.briefs import Brief, BriefError, check_brief, load_brief, load_checked_brief
from core.discovery.fetch import denied_domain

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)


SOURCE: dict[str, object] = {
    "id": "s1",
    "kind": "employer",
    "publisher": "Northwind Capital",
    "title": "Internships",
    "url": "https://careers.northwind.example/interns",
    "fetched_at": "2026-10-10T13:20:00-04:00",
}
EXEMPLARS: list[dict[str, object]] = [
    {
        "kind": "bullet",
        "text": f"Cut nightly batch latency from 40 to {n} minutes by rewriting the join",
        "source": "s1",
        "why": "System, scale and result in one line.",
    }
    for n in range(5)
]


def _body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "field": "quant-research",
        "built_at": "2026-10-10T14:00:00-04:00",
        "sources": [SOURCE],
        "claims": [{"text": "Screeners read for measured results.", "source": "s1"}],
        "exemplars": EXEMPLARS,
    }
    body.update(changes)
    return body


def _brief(**changes: object) -> Brief:
    return Brief.model_validate(yaml.safe_load(yaml.safe_dump(_body(**changes))))


def _store(home: Path, name: str, body: dict[str, object] | str) -> None:
    briefs = home / "briefs"
    briefs.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else yaml.safe_dump(body)
    (briefs / f"{name}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return tmp_path / "cb"


def test_a_complete_brief_passes() -> None:
    report = check_brief(_brief(), now=NOW)
    assert (report.verdict, report.fails, report.warns) == ("pass", (), ())


def test_a_claim_or_exemplar_citing_an_unknown_source_fails() -> None:
    report = check_brief(_brief(claims=[{"text": "Results first.", "source": "s9"}]), now=NOW)
    assert report.verdict == "fail"
    assert report.fails == ("claim 0 cites unknown source 's9'",)


def test_a_source_on_a_denied_platform_fails() -> None:
    url = "https://www.linkedin.com/pulse/how-we-hire"
    # Premise: the URL's host is a subdomain of the denied platform.
    host = urlsplit(url).hostname
    assert host == "www.linkedin.com" and denied_domain(host) == "linkedin.com"
    source = {**SOURCE, "url": url}
    report = check_brief(_brief(sources=[source]), now=NOW)
    assert report.verdict == "fail"
    assert "source 's1' is on linkedin.com" in report.fails[0]


def test_an_exemplar_over_sixty_words_fails_and_sixty_passes() -> None:
    def exemplar(words: int) -> dict[str, str]:
        return {"kind": "bullet", "text": " ".join(["word"] * words), "source": "s1", "why": "w"}

    at = _brief(exemplars=[exemplar(60)] * 5)
    over = _brief(exemplars=[exemplar(61)] + [exemplar(60)] * 4)
    assert check_brief(at, now=NOW).fails == ()
    assert check_brief(over, now=NOW).fails == ("exemplar 0 is 61 words; at most 60",)


def test_a_repeated_source_id_fails() -> None:
    report = check_brief(_brief(sources=[SOURCE, SOURCE]), now=NOW)
    assert report.fails == ("source id 's1' is listed more than once",)


def test_a_stale_source_and_too_few_exemplars_warn() -> None:
    fresh = _brief()
    assert check_brief(fresh, now=NOW + timedelta(days=89)).warns == ()
    stale = check_brief(fresh, now=NOW + timedelta(days=91))
    assert stale.verdict == "warn"
    assert stale.warns[0].startswith("source 's1' was fetched 91 days ago")
    few = check_brief(_brief(exemplars=EXEMPLARS[:4]), now=NOW)
    assert few.warns == ("4 exemplars; at least 5 expected",)


def store_brief(home: Path, field: str, exemplar: str | None = None, **changes: object) -> None:
    """Store a brief named `field` that passes its check today, unless
    `changes` break it, with `exemplar` as its first exemplar if given."""
    fresh = {**SOURCE, "fetched_at": datetime.now(tz=UTC).isoformat()}
    exemplars = EXEMPLARS if exemplar is None else [{**EXEMPLARS[0], "text": exemplar}]
    exemplars = [*exemplars, *EXEMPLARS[len(exemplars) :]]
    _store(
        home,
        field,
        _body(**{"field": field, "sources": [fresh], "exemplars": exemplars, **changes}),
    )


def test_a_blank_phrase_to_avoid_is_refused() -> None:
    """A blank phrase compiles to an empty match wherever no word character
    is on either side, as after a sentence's final full stop."""
    with pytest.raises(ValidationError, match="cannot be blank"):
        _brief(phrases_to_avoid=["synergy", "   "])


@pytest.mark.parametrize(
    ("phrase", "compound"),
    [("-driven", "A data-driven analyst"), ("driven-", "A driven-minded lead")],
)
def test_a_phrase_to_avoid_with_a_punctuation_edge_is_refused(phrase: str, compound: str) -> None:
    from core.checks.style import stock_phrases

    # Premises: an edge is not a word character, and so the whole-word match
    # misses the compound the phrase was written to catch.
    assert not (phrase[0].isalnum() and phrase[-1].isalnum())
    assert phrase in compound
    assert stock_phrases(compound, [phrase]) == []
    with pytest.raises(ValidationError, match=f"begin and end with a letter or digit: '{phrase}'"):
        _brief(phrases_to_avoid=["synergy", phrase])


def test_a_brief_that_fails_its_check_is_refused_and_one_that_warns_is_not(home: Path) -> None:
    store_brief(home, "quant-research", sources=[{**SOURCE, "url": "https://www.linkedin.com/x"}])
    with pytest.raises(BriefError, match="fails its check: source 's1' is on linkedin"):
        load_checked_brief("quant-research", now=NOW)
    store_brief(home, "quant-research", exemplars=EXEMPLARS[:4])
    assert check_brief(load_brief("quant-research"), now=NOW).verdict == "warn"  # premise
    assert load_checked_brief("quant-research", now=NOW).field == "quant-research"


def test_a_stored_brief_loads(home: Path) -> None:
    _store(home, "quant-research", _body())
    assert load_brief("quant-research").field == "quant-research"


def test_a_naive_timestamp_is_refused_on_load(home: Path) -> None:
    body = _body(built_at=datetime(2026, 10, 10, 14, 0))  # noqa: DTZ001 -- the naive value under test
    # Premise: YAML reads it as a datetime with no zone.
    parsed = yaml.safe_load(yaml.safe_dump(body))["built_at"]
    assert isinstance(parsed, datetime) and parsed.tzinfo is None
    _store(home, "quant-research", body)
    with pytest.raises(BriefError, match="timezone"):
        load_brief("quant-research")


@pytest.mark.parametrize(
    ("name", "message"),
    [("absent", "no brief named"), ("../facts", "lowercase letters"), ("Quant", "lowercase")],
)
def test_a_brief_that_cannot_be_read_is_named(home: Path, name: str, message: str) -> None:
    with pytest.raises(BriefError, match=message):
        load_brief(name)


def test_a_brief_filed_under_another_name_is_refused(home: Path) -> None:
    _store(home, "ai-ml", _body())
    with pytest.raises(BriefError, match="declares field 'quant-research'"):
        load_brief("ai-ml")


def test_the_brief_commands_report_the_check(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    _store(home, "quant-research", _body())
    assert main(["brief", "check", "quant-research"]) == 0
    assert main(["brief", "show", "quant-research"]) == 0
    shown = capsys.readouterr().out
    assert "imitate the structure, never the words" in shown
    assert "[Northwind Capital, Internships]" in shown
    assert main(["brief", "check", "absent"]) == 3


def test_a_brief_that_fails_its_check_is_not_shown(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    _store(
        home, "quant-research", _body(sources=[{**SOURCE, "url": "https://lnkd.linkedin.com/x"}])
    )
    assert main(["brief", "check", "quant-research"]) == 2
    assert main(["brief", "show", "quant-research"]) == 2
    assert "Exemplars" not in capsys.readouterr().out


def test_the_brief_tool_returns_the_brief_and_its_check(home: Path) -> None:
    import asyncio

    from core.mcp_server import server

    _store(home, "quant-research", _body())
    result = asyncio.run(server.call_tool("brief", {"field": "quant-research"}))
    content = result.structured_content  # type: ignore[attr-defined]
    assert content["check"] == {"verdict": "pass", "fails": [], "warns": []}
    assert content["brief"]["field"] == "quant-research"


def test_a_brief_that_is_not_utf8_is_reported_unreadable(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    raw = b"\xff\xfe field: quant-research"
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    (home / "briefs").mkdir(parents=True, exist_ok=True)
    (home / "briefs" / "quant-research.yaml").write_bytes(raw)
    assert main(["brief", "check", "quant-research"]) == 3
    assert main(["brief", "show", "quant-research"]) == 3
    assert "quant-research" in capsys.readouterr().err


def test_a_brief_linked_from_outside_the_home_is_reported_unreadable(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    outside = tmp_path / "elsewhere.yaml"
    outside.write_text(yaml.safe_dump(_body()), encoding="utf-8")
    (home / "briefs").mkdir(parents=True, exist_ok=True)
    link = home / "briefs" / "quant-research.yaml"
    link.symlink_to(outside)
    assert link.is_symlink()
    assert not link.resolve().is_relative_to(home.resolve())
    assert main(["brief", "check", "quant-research"]) == 3
    assert main(["brief", "show", "quant-research"]) == 3
    assert "outside the callback home" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["https://\\linkedin.com/x", "https://", "https://[x/a"])
def test_a_source_url_without_a_plain_host_is_refused(home: Path, url: str) -> None:
    _store(home, "quant-research", _body(sources=[{**SOURCE, "url": url}]))
    with pytest.raises(BriefError, match="cannot read brief"):
        load_brief("quant-research")


def test_a_denied_but_plain_host_still_loads(home: Path) -> None:
    _store(home, "quant-research", _body(sources=[{**SOURCE, "url": "https://LinkedIn.com./x"}]))
    assert load_brief("quant-research").sources[0].url == "https://LinkedIn.com./x"
