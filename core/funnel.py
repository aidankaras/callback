"""Where applications go after they are sent, by the source they were sent through.

A *reply* is an outcome a person at the employer produced: a callback, an
interview, an offer or a rejection. "viewed" is a system event and
"ghosted" and "withdrew" are the candidate's own records, so none of them
is a reply. A *positive* reply is a callback, an interview or an offer.

Days to reply are measured from submission to the first reply. An
application with no outcome at all after `STALE_DAYS` is listed as stale,
as a suggestion to record it as ghosted; nothing here records anything.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.apply import Application

STALE_DAYS = 21
REPLIES = frozenset({"callback", "interview", "offer", "rejected"})
POSITIVE = frozenset({"callback", "interview", "offer"})


@dataclass(frozen=True)
class Row:
    source: str
    applied: int
    replied: int
    positive: int
    interviews: int
    offers: int
    median_days_to_reply: float | None

    @property
    def reply_rate(self) -> float:
        return self.replied / self.applied if self.applied else 0.0

    @property
    def positive_rate(self) -> float:
        return self.positive / self.applied if self.applied else 0.0


@dataclass(frozen=True)
class Funnel:
    rows: tuple[Row, ...]
    total: Row
    stale: tuple[Application, ...]


def _row(source: str, applications: Sequence[Application]) -> Row:
    days: list[float] = []
    replied = positive = interviews = offers = 0
    for a in applications:
        kinds = {o.kind for o in a.outcomes}
        first = next((o for o in a.outcomes if o.kind in REPLIES), None)
        if first is not None:
            replied += 1
            days.append((first.occurred_at - a.submitted_at).total_seconds() / 86400)
        positive += bool(kinds & POSITIVE)
        interviews += bool(kinds & {"interview", "offer"})
        offers += "offer" in kinds
    return Row(
        source=source,
        applied=len(applications),
        replied=replied,
        positive=positive,
        interviews=interviews,
        offers=offers,
        median_days_to_reply=statistics.median(days) if days else None,
    )


def funnel(applications: Sequence[Application], *, now: datetime) -> Funnel:
    """The funnel for every source, most-used first, and a total."""
    by_source: dict[str, list[Application]] = {}
    for a in applications:
        by_source.setdefault(a.source, []).append(a)
    rows = sorted(
        (_row(source, apps) for source, apps in by_source.items()),
        key=lambda r: (-r.applied, r.source),
    )
    stale = tuple(
        a
        for a in applications
        if not a.outcomes and (now - a.submitted_at).total_seconds() / 86400 >= STALE_DAYS
    )
    return Funnel(rows=tuple(rows), total=_row("all", applications), stale=stale)


def render(result: Funnel) -> str:
    """A plain-text table, then the stale applications."""

    def line(r: Row) -> str:
        days = f"{r.median_days_to_reply:.0f}d" if r.median_days_to_reply is not None else "-"
        return (
            f"{r.source:<12} {r.applied:>7} {r.replied:>7} {r.reply_rate:>6.0%} "
            f"{r.positive:>8} {r.positive_rate:>6.0%} {r.interviews:>10} {r.offers:>6} {days:>12}"
        )

    header = (
        f"{'source':<12} {'applied':>7} {'replied':>7} {'rate':>6} "
        f"{'positive':>8} {'rate':>6} {'interviews':>10} {'offers':>6} {'median reply':>12}"
    )
    out = [header, *(line(r) for r in result.rows), line(result.total)]
    if result.stale:
        out += [
            "",
            f"No reply after {STALE_DAYS}+ days (record with `callback outcome <job> ghosted`):",
        ]
        out += [
            f"  {a.submitted_at:%Y-%m-%d}  {a.job_id}  {a.company} / {a.title}"
            for a in result.stale
        ]
    return "\n".join(out)
