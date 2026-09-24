"""Every expected count below is worked by hand from APPS, not computed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from core.apply import Application
from core.insights import insights, render, sends
from core.mcp_server import server

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _sent(
    template: str | None, family: str | None, facts: list[str], day: float = 0
) -> dict[str, object]:
    return {
        "at": T0 + timedelta(days=day),
        "resume_sha256": "0" * 64,
        "based_on": template,
        "role_family": family,
        "cited_fact_ids": facts,
    }


def _app(
    job: str, sent: list[dict[str, object]], *outcomes: tuple[str, float, int | None]
) -> Application:
    """Outcomes are (kind, days after T0, the send they are attributed to)."""
    return Application.model_validate(
        {
            "job_id": job,
            "company": "Acme",
            "title": "Intern",
            "source": "greenhouse",
            "submitted_at": T0,
            "resume_sha256": "0" * 64,
            "sent": sent,
            "outcomes": [
                {"kind": k, "occurred_at": T0 + timedelta(days=d), "sent_index": i}
                for k, d, i in outcomes
            ],
        }
    )


ML = ["etl", "tuning", "nw"]
QUANT = ["nw", "pricing"]
APPS = [
    _app("a1", [_sent("ml-base", "ml-infra", ML)], ("interview", 3, 0)),
    _app("a2", [_sent("ml-base", "ml-infra", ML)], ("rejected", 5, 0)),
    _app("a3", [_sent("ml-base", "quant", ML)]),
    _app("a4", [_sent("ml-base", "quant", ML)], ("callback", 4, 0)),
    # Refreshed onto the quant template two days in: "viewed" belongs to the
    # first packet, the rejection four days after the second to the second.
    _app(
        "a5",
        [_sent("ml-base", "ml-infra", ["etl", "nw"]), _sent("quant-base", "quant", QUANT, day=2)],
        ("viewed", 1, 0),
        ("rejected", 6, 1),
    ),
    _app("b1", [_sent("quant-base", "quant", QUANT)], ("rejected", 10, 0)),
    _app("b2", [_sent("quant-base", None, QUANT)]),
    _app("b3", [_sent("quant-base", "quant", QUANT)], ("interview", 2, 0), ("offer", 9, 0)),
    # Recorded before sends were kept.
    _app("l1", [], ("rejected", 7, None)),
]


def _rows(rows: tuple[object, ...]) -> dict[str, tuple[int, int, int]]:
    return {r.source: (r.applied, r.replied, r.positive) for r in rows}  # type: ignore[attr-defined]


def test_sends_split_a_refreshed_application_and_keep_legacy_as_unknown() -> None:
    items = sends(APPS)
    assert len(items) == 10  # 8 applications, one refreshed, one legacy
    a5 = [s for s in items if s.view.job_id == "a5"]
    assert [[o.kind for o in s.view.outcomes] for s in a5] == [["viewed"], ["rejected"]]
    assert sum(s.sent is None for s in items) == 1


def test_counts_by_template() -> None:
    """ml-base: a1-a4 and a5's first packet. Replies: a1 interview, a2 rejected,
    a4 callback; a5's "viewed" is not a reply. quant-base: a5's second packet,
    b1-b3; replies a5 and b1 rejected, b3 interview."""
    result = insights(APPS)
    assert result.sends == 10
    assert _rows(result.by_template) == {
        "ml-base": (5, 3, 2),
        "quant-base": (4, 3, 1),
        "unknown": (1, 1, 0),
    }
    ml, quant, unknown = result.by_template
    assert (ml.comparable, quant.comparable, unknown.comparable) == (True, False, False)


def test_days_to_reply_run_from_the_send_not_the_application() -> None:
    """quant-base replies: a5 four days after its second packet (day 2 -> 6),
    b1 on day 10, b3 on day 2: median 4. Measured from the application date,
    a5 would be 6 and the median 6."""
    quant = next(r for r in insights(APPS).by_template if r.source == "quant-base")
    assert quant.median_days_to_reply == 4.0


def test_counts_by_role_family() -> None:
    """ml-infra: a1, a2, a5's first. quant: a3, a4, a5's second, b1, b3."""
    assert _rows(insights(APPS).by_role_family) == {
        "quant": (5, 4, 2),
        "ml-infra": (3, 2, 1),
        "(no role family)": (1, 0, 0),
        "unknown": (1, 1, 0),
    }


def test_counts_by_cited_fact_with_a_tie_listed_by_name() -> None:
    """tuning (a1-a4) and pricing (a5's second, b1-b3) are both 4 sends with 3
    replies, and tuning has more positive ones: a tie, listed by name, so
    pricing comes first. Mutation: break ties on positive replies -- fails."""
    rows = insights(APPS).by_fact
    assert [r.source for r in rows] == ["nw", "etl", "pricing", "tuning", "unknown"]
    assert _rows(rows) == {
        "nw": (9, 6, 3),
        "etl": (5, 3, 2),
        "tuning": (4, 3, 2),
        "pricing": (4, 3, 1),
        "unknown": (1, 1, 0),
    }


def test_the_rendering_is_counts_and_marks_small_groups() -> None:
    text = render(insights(APPS))
    assert "%" not in text
    ml = next(line for line in text.splitlines() if line.startswith("ml-base"))
    quant = next(line for line in text.splitlines() if line.startswith("quant-base"))
    assert "3 of 5" in ml and not ml.endswith("too few to compare")
    assert "3 of 4" in quant and quant.endswith("too few to compare")
    for word in ("best", "better", "worse", "significant"):
        assert word not in text.casefold()


def test_no_applications_is_no_sends() -> None:
    result = insights([])
    assert (result.sends, result.by_template, result.by_fact) == (0, (), ())


def test_the_tool_reports_counts_and_never_a_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.apply.load_applications", lambda: APPS)
    called = asyncio.run(server.call_tool("insights", {}))
    result = cast("dict[str, Any]", called.structured_content)  # type: ignore[attr-defined]
    assert result["sends"] == 10
    ml = next(r for r in result["by_template"] if r["group"] == "ml-base")
    assert ml == {
        "group": "ml-base",
        "sent": 5,
        "replied": 3,
        "positive": 2,
        "interviews": 1,
        "offers": 0,
        "comparable": True,
    }
    assert not any("rate" in key for r in result["by_fact"] for key in r)


def test_the_cli_prints_the_tables(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli import main

    monkeypatch.setattr("core.apply.load_applications", lambda: APPS)
    assert main(["insights"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("10 packet(s) sent.")
    assert "cited fact" in out and "pricing" in out
