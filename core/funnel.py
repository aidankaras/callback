"""Where applications go after they are sent, by the source they were sent through.

A *reply* is an outcome a person at the employer produced: a callback, an
interview, an offer or a rejection. "viewed" is a system event and
"ghosted" and "withdrew" are the candidate's own records, so none of them
is a reply. A *positive* reply is a callback, an interview or an offer.

Days to reply are measured from submission to the first reply. An
application with no outcome at all after `STALE_DAYS` is listed as stale,
as a suggestion to record it as ghosted; nothing here records anything.

A search sends tens of applications, not thousands, so a rate here is a count
and never a finding. Every rate is printed as its counts ("2 of 3"), and a
group of fewer than `MIN_GROUP` is marked too few to compare. Nothing ranks
groups or calls one better. `core.insights` groups by other keys and reuses
`summarize`, so the two cannot disagree about what a reply is.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from core.apply import Application

STALE_DAYS = 21
MIN_GROUP = 5
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

    @property
    def comparable(self) -> bool:
        """Whether the group is large enough that its counts are worth setting beside another's."""
        return self.applied >= MIN_GROUP


@dataclass(frozen=True)
class Funnel:
    rows: tuple[Row, ...]
    total: Row
    stale: tuple[Application, ...]


def summarize(source: str, applications: Sequence[Application]) -> Row:
    """One row: how many of `applications` were replied to, and how."""
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
        (summarize(source, apps) for source, apps in by_source.items()),
        key=lambda r: (-r.applied, r.source),
    )
    stale = tuple(
        a
        for a in applications
        if not a.outcomes and (now - a.submitted_at).total_seconds() / 86400 >= STALE_DAYS
    )
    return Funnel(rows=tuple(rows), total=summarize("all", applications), stale=stale)


def line(r: Row, width: int = 12) -> str:
    """One row as counts, never bare percentages, marked when it is too small to compare."""
    days = f"{r.median_days_to_reply:.0f}d" if r.median_days_to_reply is not None else "-"
    replied = f"{r.replied} of {r.applied}"
    positive = f"{r.positive} of {r.applied}"
    note = "" if r.comparable else "  too few to compare"
    return (
        f"{r.source:<{width}} {r.applied:>7} {replied:>9} {positive:>9} "
        f"{r.interviews:>10} {r.offers:>6} {days:>12}{note}"
    )


def header(label: str, width: int = 12, counted: str = "applied") -> str:
    return (
        f"{label:<{width}} {counted:>7} {'replied':>9} {'positive':>9} "
        f"{'interviews':>10} {'offers':>6} {'median reply':>12}"
    )


def render(result: Funnel) -> str:
    """A plain-text table, then the stale applications."""
    out = [header("source"), *(line(r) for r in result.rows), line(result.total)]
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
