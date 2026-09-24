from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.apply import Application, Outcome
from core.funnel import STALE_DAYS, funnel, render

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _app(job: str, source: str, days_ago: float, *outcomes: tuple[str, float]) -> Application:
    """An application sent `days_ago`, with each outcome `n` days after sending."""
    sent = NOW - timedelta(days=days_ago)
    return Application.model_validate(
        {
            "job_id": job,
            "company": "Acme",
            "title": "Intern",
            "source": source,
            "submitted_at": sent,
            "resume_sha256": "0" * 64,
            "outcomes": [
                Outcome(kind=kind, occurred_at=sent + timedelta(days=n))  # type: ignore[arg-type]
                for kind, n in outcomes
            ],
        }
    )


APPS = [
    # greenhouse: 3 sent; replies after 4 and 10 days; one interview that became an offer.
    _app("g1", "greenhouse", 30, ("viewed", 1), ("interview", 4), ("offer", 12)),
    _app("g2", "greenhouse", 30, ("rejected", 10)),
    _app("g3", "greenhouse", 25),
    # lever: 1 sent, viewed and then ghosted -- no reply at all.
    _app("l1", "lever", 40, ("viewed", 2), ("ghosted", 30)),
    # other: sent 3 days ago, too recent to be stale.
    _app("o1", "other", 3),
]


def test_rates_and_medians_by_source() -> None:
    """Worked by hand from APPS. Mutations: count "viewed" as a reply -- lever
    replies 1; measure to the last reply -- greenhouse median becomes 11."""
    result = funnel(APPS, now=NOW)
    rows = {r.source: r for r in result.rows}
    g = rows["greenhouse"]
    assert (g.applied, g.replied, g.positive, g.interviews, g.offers) == (3, 2, 1, 1, 1)
    assert g.median_days_to_reply == 7.0  # median of 4 and 10
    lv = rows["lever"]
    assert (lv.applied, lv.replied, lv.positive, lv.median_days_to_reply) == (1, 0, 0, None)
    assert [r.source for r in result.rows] == ["greenhouse", "lever", "other"]
    t = result.total
    assert (t.applied, t.replied, t.positive, t.offers) == (5, 2, 1, 1)
    assert t.reply_rate == 2 / 5


def test_only_silent_applications_old_enough_are_stale() -> None:
    """g3 is 25 days old with nothing; o1 is 3 days old; l1 is ghosted already.
    Mutation: ignore the age -- o1 is listed too."""
    assert STALE_DAYS <= 25
    assert [a.job_id for a in funnel(APPS, now=NOW).stale] == ["g3"]


def test_the_rendering_names_each_source_and_what_to_record() -> None:
    text = render(funnel(APPS, now=NOW))
    assert text.splitlines()[1].startswith("greenhouse")
    assert "callback outcome <job> ghosted" in text
    assert "g3" in text and "o1" not in text


def test_every_rate_is_printed_as_counts_and_small_groups_say_so() -> None:
    """Greenhouse sent 3 and heard back from 2: that prints as counts, never as 67%.
    Mutation: set MIN_GROUP to 3 -- greenhouse is no longer marked."""
    lines = render(funnel(APPS, now=NOW)).splitlines()
    greenhouse = next(line for line in lines if line.startswith("greenhouse"))
    assert "2 of 3" in greenhouse and "1 of 3" in greenhouse
    assert "%" not in "\n".join(lines)
    assert greenhouse.endswith("too few to compare")
    total = next(line for line in lines if line.startswith("all"))
    assert "2 of 5" in total and not total.endswith("too few to compare")  # 5 is enough
