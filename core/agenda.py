"""What to do next, from what is already recorded.

Every part of this exists as its own command -- ranked postings, application
records, outcomes, drafts on disk. What was missing is the question a
candidate actually has on a given evening: of all that, what should I do now?

Nothing here judges anything new. It reads the postings that were watched,
the applications that were recorded and the drafts that were built, and
orders them by what is time-sensitive: a posting is worth applying to while
it is fresh, an interview is worth preparing for once it is booked, and an
application with no reply after `STALE_DAYS` is worth marking rather than
waiting on. It never acts: every item names the command that would.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.apply import MAX_PER_DAY, Application
from core.discovery.store import Found
from core.funnel import POSITIVE, REPLIES, STALE_DAYS
from core.paths import resolve_inside
from core.profile.models import Fact
from core.triage import Profile, rank

# How many postings to suggest at once: a shortlist a person will actually
# read, not the whole ranked list, which `callback triage` already prints.
SHORTLIST = 5
# An application is waiting on nobody once it has a reply or the candidate has
# closed it themselves.
_CLOSED = REPLIES | POSITIVE | {"ghosted", "withdrew"}


@dataclass(frozen=True)
class Item:
    """One thing worth doing, and the command that does it."""

    kind: str
    what: str
    command: str


@dataclass(frozen=True)
class Agenda:
    items: tuple[Item, ...]
    applied_today: int

    @property
    def headroom(self) -> int:
        return max(0, MAX_PER_DAY - self.applied_today)


def _drafted_jobs() -> set[str]:
    outputs = resolve_inside("outputs")
    if not outputs.is_dir():
        return set()
    built: set[str] = set()
    for draft in outputs.glob("*/draft"):
        # A rendered PDF is not a passed document: `build` writes one whatever
        # the gates said, and a draft that failed is not work ready to send.
        try:
            report = json.loads((draft / "report.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("verdict") in {"pass", "warn"} and (draft / "resume.pdf").is_file():
            built.add(draft.parent.name)
    return built


def _packet_jobs() -> set[str]:
    outputs = resolve_inside("outputs")
    if not outputs.is_dir():
        return set()
    return {d.parent.name for d in outputs.glob("*/packet") if d.is_dir()}


def _prepared_jobs() -> set[str]:
    outputs = resolve_inside("outputs")
    if not outputs.is_dir():
        return set()
    return {d.parent.name for d in outputs.glob("*/interview") if (d / "prep.md").is_file()}


def agenda(
    postings: Sequence[Found],
    applications: Sequence[Application],
    profile: Profile,
    facts: Mapping[str, Fact],
    *,
    now: datetime,
) -> Agenda:
    """What to do now, most time-sensitive first."""
    items: list[Item] = []
    applied_to = {a.job_id for a in applications}
    taken = {a.company.casefold() for a in applications}

    # An interview already booked outranks anything else: it has a date.
    prepared = _prepared_jobs()
    for application in applications:
        kinds = {o.kind for o in application.outcomes}
        if kinds & {"interview", "offer"} and application.job_id not in prepared:
            items.append(
                Item(
                    "interview",
                    f"{application.company} / {application.title}: an interview is recorded "
                    "and no prep pack has been built",
                    f"callback interview {application.job_id}",
                )
            )

    # A draft that passed but was never sent is work already done.
    for job in sorted(_drafted_jobs() - applied_to - _packet_jobs()):
        items.append(
            Item(
                "unsent",
                f"{job}: a built draft that was never applied with",
                f"callback apply {job} --company '<company>' --title '<title>' --source <source>",
            )
        )

    # Then the freshest postings worth applying to, skipping companies already
    # applied to: a second application to the same employer is a decision, not
    # a suggestion.
    ranked = rank(list(postings), profile, facts, now=now)
    suggested = 0
    for row in ranked:
        if suggested >= SHORTLIST:
            break
        if row.posting.id in applied_to or row.posting.company.casefold() in taken:
            continue
        if row.eligibility.verdict == "knockout":
            continue
        evidenced, missing = row.coverage
        items.append(
            Item(
                "apply",
                f"{row.posting.company} / {row.posting.title}: {row.age_days}d old, "
                f"{len(evidenced)} of {len(evidenced) + len(missing)} named technologies backed",
                f"callback postings take {row.posting.id} --job <job-id>",
            )
        )
        suggested += 1

    # Last, the applications that have gone quiet.
    for application in applications:
        # Anything the candidate has already closed -- a reply, a withdrawal,
        # a ghosting they recorded -- is not waiting on them.
        if any(o.kind in _CLOSED for o in application.outcomes):
            continue
        waited = (now - application.submitted_at) / timedelta(days=1)
        if waited >= STALE_DAYS:
            items.append(
                Item(
                    "silent",
                    f"{application.company} / {application.title}: {waited:.0f} days, no reply",
                    f"callback outcome {application.job_id} ghosted",
                )
            )

    today = sum(1 for a in applications if (now - a.submitted_at) < timedelta(days=1))
    return Agenda(items=tuple(items), applied_today=today)


def render(result: Agenda) -> str:
    """The agenda as lines to read, grouped by kind in the order they were built."""
    width = max((len(item.kind) for item in result.items), default=0)
    lines = [
        f"{item.kind.upper():<{width}}  {item.what}\n{'':<{width}}  {item.command}"
        for item in result.items
    ] or ["Nothing waiting. `callback watch` for new postings."]
    # The headroom is printed even with nothing else to say: it is the number
    # that decides whether today's work can be sent at all.
    lines.append(f"\n{result.headroom} of {MAX_PER_DAY} applications left in the next 24 hours.")
    return "\n".join(lines)
