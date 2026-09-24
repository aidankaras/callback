"""What has happened to what was sent, grouped by what it was built from.

`funnel` groups applications by the channel they went through. This groups
*sends* -- each packet handed over, the first by `apply` and one more for each
`--refresh` -- by the template the draft began as, the role family, and each
fact the resume cited. A send's outcomes are the ones recorded while it was
the current packet (`Outcome.sent_index`), so a refresh never re-attributes
what happened to the packet before it.

A send whose record predates provenance is counted under "unknown": the
original packet of an application recorded before sends were kept, or the
first packet of one refreshed since. Nothing is guessed for it.

The rows come from `core.funnel.summarize`, so a reply means here exactly what
it means there, and the same rules hold: counts always, groups under
`MIN_GROUP` marked too few to compare, no ranking and no verdict. At the
volume one search produces, a difference between two groups is almost
always noise, and saying otherwise would be inventing a finding.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from core.apply import Application, Sent
from core.funnel import Row, header, line, summarize
from core.paths import resolve_inside

UNKNOWN = "unknown"
NO_TEMPLATE = "(no template)"
NO_FAMILY = "(no role family)"


@dataclass(frozen=True)
class Send:
    """One packet, as an `Application` whose date and outcomes are this packet's own."""

    sent: Sent | None
    view: Application


@dataclass(frozen=True)
class Insights:
    sends: int
    by_template: tuple[Row, ...]
    by_role_family: tuple[Row, ...]
    by_fact: tuple[Row, ...]


def sends(applications: Iterable[Application]) -> list[Send]:
    """Every packet sent, each with only the outcomes attributed to it."""
    out: list[Send] = []
    for a in applications:
        first_recorded = bool(a.sent) and a.sent[0].at == a.submitted_at
        if not first_recorded:
            legacy = [o for o in a.outcomes if o.sent_index is None]
            out.append(Send(None, a.model_copy(update={"outcomes": legacy})))
        for i, s in enumerate(a.sent):
            mine = [o for o in a.outcomes if o.sent_index == i]
            out.append(Send(s, a.model_copy(update={"submitted_at": s.at, "outcomes": mine})))
    return out


def _grouped(items: Sequence[Send], keys: Callable[[Send], Iterable[str]]) -> tuple[Row, ...]:
    groups: dict[str, list[Application]] = {}
    for item in items:
        for key in keys(item):
            groups.setdefault(key, []).append(item.view)
    rows = (summarize(key, views) for key, views in groups.items())
    # Ordered by size and then name so the listing is stable; not a ranking.
    return tuple(sorted(rows, key=lambda r: (-r.applied, r.source)))


def insights(applications: Iterable[Application]) -> Insights:
    items = sends(applications)
    return Insights(
        sends=len(items),
        by_template=_grouped(
            items, lambda s: [UNKNOWN if s.sent is None else s.sent.based_on or NO_TEMPLATE]
        ),
        by_role_family=_grouped(
            items, lambda s: [UNKNOWN if s.sent is None else s.sent.role_family or NO_FAMILY]
        ),
        by_fact=_grouped(items, lambda s: [UNKNOWN] if s.sent is None else s.sent.cited_fact_ids),
    )


def rejected_postings(applications: Iterable[Application]) -> tuple[list[str], list[str]]:
    """The saved posting text of every rejected application, and the jobs
    rejected with no posting saved (`inbox/<job>.md`), which a count built
    from the texts alone would silently leave out."""
    texts: list[str] = []
    missing: list[str] = []
    for a in applications:
        if not any(o.kind == "rejected" for o in a.outcomes):
            continue
        path = resolve_inside(f"inbox/{a.job_id}.md")
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        # An empty file names nothing, which reads the same as a zero.
        if text.strip():
            texts.append(text)
        else:
            missing.append(a.job_id)
    return texts, missing


def render(result: Insights) -> str:
    """Three tables of counts. A send counts once per fact it cites, so the
    fact table's rows overlap and do not add up to the number of sends."""
    width = max(
        [
            12,
            *(
                len(r.source)
                for r in (*result.by_template, *result.by_role_family, *result.by_fact)
            ),
        ]
    )
    out = [f"{result.sends} packet(s) sent. Counts only; no group is ranked."]
    for title, rows in (
        ("template", result.by_template),
        ("role family", result.by_role_family),
        ("cited fact", result.by_fact),
    ):
        out += ["", header(title, width, "sent"), *(line(r, width) for r in rows)]
    return "\n".join(out)
