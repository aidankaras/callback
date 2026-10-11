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

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from core.apply import MAX_PER_DAY, Application, Target, same_url
from core.discovery.store import Found
from core.funnel import POSITIVE, REPLIES, STALE_DAYS
from core.paths import resolve_inside
from core.profile.models import Fact
from core.triage import Profile, rank

# How many postings to suggest at once: a shortlist a person will actually
# read, not the whole ranked list, which `callback triage` already prints.
SHORTLIST = 5
# How far ahead a deadline is worth listing, and how long a passed one stays
# listed as closed, so that it is seen at least once rather than vanishing overnight.
DEADLINE_DAYS = 30
CLOSED_DAYS = 7
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


def _drafted_jobs() -> tuple[set[str], set[str]]:
    """`(ready, unreadable)`: the jobs whose draft is work ready to send, and
    those whose report could not be read.

    A rendered PDF is not a passed document -- `build` writes one whatever the
    gates said -- and a verdict is about the bytes it names, so a document
    edited since the build is not ready either: every consumer (`apply`,
    `finalize`, `template save`) checks that hash and would refuse it.

    A report that is missing is simply a job never built. A report that exists
    and cannot be read is a different thing, and this command's whole job is
    not to forget work already done, so it is returned rather than dropped.
    """
    outputs = resolve_inside("outputs")
    if not outputs.is_dir():
        return set(), set()
    ready: set[str] = set()
    unreadable: set[str] = set()
    for draft in sorted(outputs.glob("*/draft")):
        job = draft.parent.name
        try:
            parsed: object = json.loads((draft / "report.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            unreadable.add(job)
            continue
        if not isinstance(parsed, dict):
            unreadable.add(job)
            continue
        report = cast("dict[str, object]", parsed)
        document = draft / "resume.yaml"
        if report.get("verdict") not in {"pass", "warn"} or not (draft / "resume.pdf").is_file():
            continue
        try:
            current = hashlib.sha256(document.read_bytes()).hexdigest()
        except OSError:
            unreadable.add(job)
            continue
        if current == report.get("doc_sha256"):
            ready.add(job)
        else:
            unreadable.add(job)
    return ready, unreadable


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

    # A deadline is a date, like an interview: soonest first.
    items.extend(_deadlines(postings, applications, now=now))

    # A draft that passed but was never sent is work already done.
    ready, unreadable = _drafted_jobs()
    packets = _packet_jobs()
    for job in sorted(ready - applied_to - packets):
        items.append(
            Item(
                "unsent",
                f"{job}: a built draft that was never applied with",
                f"callback apply {job} --company '<company>' --title '<title>' --source <source>",
            )
        )
    for job in sorted(unreadable - applied_to - packets):
        items.append(
            Item(
                "rebuild",
                f"{job}: a draft whose gates cannot be read, or whose document changed since",
                f"callback build $CALLBACK_HOME/outputs/{job}/draft/resume.yaml --job {job}",
            )
        )

    # Then the freshest postings worth applying to, skipping companies already
    # applied to: a second application to the same employer is a decision, not
    # a suggestion. A posting already applied to under another spelling of its
    # company is skipped by the test the deadlines use.
    ranked = rank(list(postings), profile, facts, now=now)
    suggested = 0
    for row in ranked:
        if suggested >= SHORTLIST:
            break
        if row.posting.id in applied_to or row.posting.company.casefold() in taken:
            continue
        if any(_is_applied(a, _target(row.posting)) for a in applications):
            continue
        if row.eligibility.verdict == "knockout":
            continue
        if row.posting.deadline is not None and row.posting.deadline < now:
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


def _term(title: str) -> str | None:
    """The trailing parenthetical of a title, "(Summer 2027)", normalised."""
    match = re.search(r"\(([^()]*)\)\s*$", title)
    return " ".join(match.group(1).casefold().split()) if match else None


def _target(posting: Found) -> Target:
    """A stored posting as `apply` describes one: applications record no posting id."""
    return Target(
        job_id="posting",
        company=posting.company,
        title=posting.title,
        location=posting.location,
        url=posting.url,
        source="other",
    )


def _is_applied(application: Application, posting: Target) -> bool:
    """Whether `application` was made to `posting`, leaning the other way from
    `same_posting`, which leans towards matching so that `apply` never reopens
    a posting; here that bias would hide a deadline or a suggestion.

    Two URLs, when both are given, decide it either way: a last cycle's
    "/2026" application is not this cycle's "/2027" posting, whatever the
    title. With a URL missing on either side, `same_posting`'s company and
    title rule applies, except that titles differing only in a bracketed term
    ("(Summer 2027)" against "(Winter 2027)") are different postings."""
    if application.url and posting.url:
        return same_url(application.url, posting.url)
    if not application.same_posting(posting):
        return False
    a, b = _term(application.title), _term(posting.title)
    return a is None or b is None or a == b


def _deadlines(
    postings: Sequence[Found], applications: Sequence[Application], *, now: datetime
) -> list[Item]:
    """Postings closing in the next `DEADLINE_DAYS` days, or closed in the last
    `CLOSED_DAYS`, that have not been applied to. Days are counted at the
    deadline's own UTC offset: the one typed with it, or for a bare date this
    machine's offset on that date."""
    dated: list[tuple[datetime, Found]] = []
    for posting in postings:
        deadline = posting.deadline
        if deadline is None or not now - timedelta(days=CLOSED_DAYS) <= deadline:
            continue
        if deadline > now + timedelta(days=DEADLINE_DAYS):
            continue
        this = _target(posting)
        if not any(_is_applied(a, this) for a in applications):
            dated.append((deadline, posting))
    items: list[Item] = []
    for deadline, posting in sorted(dated, key=lambda pair: pair[0]):
        day = deadline.date().isoformat()
        if deadline < now:
            when = f"closed ({day})"
        else:
            days = (deadline.date() - now.astimezone(deadline.tzinfo).date()).days
            when = f"closes in {days} days ({day})"
        items.append(
            Item(
                "deadline",
                f"{posting.company} {posting.title}: {when}",
                f"callback postings take {posting.id} --job <job-id>",
            )
        )
    return items


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
