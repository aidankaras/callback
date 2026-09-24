"""Grounding failures that keep coming back, traced to the fact that causes them.

When the same bullet fails for the same reason in job after job -- "'ETL'
appears in no cited fact" on bullets citing `etl` -- the fix belongs in the
fact, once, not in every draft. This reads each job's latest build record
(`outputs/<job>/draft/report.json`) and reports every failure found in at
least `MIN_JOBS` of them.

Only the latest build of each job counts. Rebuilding one draft five times
while fixing it is one job, not five, and a failure fixed and rebuilt
disappears from the next report without anything being cleared.

A record that cannot be read is listed by job, never skipped. So is one
whose grounding failed without naming any fact: a record from before per-fact
findings were kept, a bullet citing a fact that does not exist, or a gate
that crashed. Its failures could recur unseen, so a report of no recurring
failures means every record was read and every grounding failure in them was
traced to a fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from core.paths import resolve_inside

MIN_JOBS = 2


@dataclass(frozen=True)
class Recurring:
    kind: str
    facts: tuple[str, ...]
    detail: str
    jobs: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    recurring: tuple[Recurring, ...]
    unreadable: tuple[str, ...]
    untraced: tuple[str, ...] = ()


Key = tuple[str, tuple[str, ...], str]


def _findings(record: object) -> set[Key] | None:
    """The distinct per-fact findings in one build record, or `None` if it is malformed.

    Malformed includes a record with no grounding outcome: every build writes one.
    """
    if not isinstance(record, dict):
        return None
    body = cast("dict[str, object]", record)
    if _grounding_verdict(body) is None:
        return None
    raw = body.get("fact_findings", [])
    if not isinstance(raw, list):
        return None
    found: set[Key] = set()
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            return None
        entry = cast("dict[str, object]", item)
        kind, facts, detail = entry.get("kind"), entry.get("facts"), entry.get("detail")
        if not (isinstance(kind, str) and isinstance(detail, str) and isinstance(facts, list)):
            return None
        ids = cast("list[object]", facts)
        if not all(isinstance(f, str) for f in ids):
            return None
        found.add((kind, tuple(cast("list[str]", ids)), detail))
    return found


def _grounding_verdict(body: dict[str, object]) -> str | None:
    outcomes = body.get("outcomes")
    if not isinstance(outcomes, list):
        return None
    for item in cast("list[object]", outcomes):
        if isinstance(item, dict):
            entry = cast("dict[str, object]", item)
            verdict = entry.get("verdict")
            if entry.get("gate") == "grounding" and isinstance(verdict, str):
                return verdict
    return None


def recurring() -> Report:
    outputs = resolve_inside("outputs")
    if not outputs.is_dir():
        return Report((), ())
    seen: dict[Key, list[str]] = {}
    unreadable: list[str] = []
    untraced: list[str] = []
    for path in sorted(outputs.glob("*/draft/report.json")):
        job = path.parent.parent.name
        try:
            body: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            body = None
        found = _findings(body)
        if found is None:
            unreadable.append(job)
            continue
        if not found and _grounding_verdict(cast("dict[str, object]", body)) == "fail":
            untraced.append(job)
        for key in found:
            seen.setdefault(key, []).append(job)
    hits = [
        Recurring(kind, facts, detail, tuple(jobs))
        for (kind, facts, detail), jobs in seen.items()
        if len(jobs) >= MIN_JOBS
    ]
    hits.sort(key=lambda r: (-len(r.jobs), r.facts, r.kind, r.detail))
    return Report(tuple(hits), tuple(unreadable), tuple(untraced))


def render(report: Report) -> str:
    lines = [
        f"{len(r.jobs)} jobs: bullets citing {', '.join(r.facts)} -- {r.kind}: {r.detail}"
        f" ({', '.join(r.jobs)}); fix the fact once"
        for r in report.recurring
    ]
    if report.unreadable:
        lines.append(f"could not read the build record for: {', '.join(report.unreadable)}")
    if report.untraced:
        lines.append(
            f"grounding failed without naming a fact in: {', '.join(report.untraced)}; "
            "rebuild them, or read their report.md"
        )
    return "\n".join(lines) or "no grounding failure recurs across jobs"
