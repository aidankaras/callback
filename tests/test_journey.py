"""The documented journey, end to end, through the commands themselves.

Every other test covers one module. This one walks what `docs/using-callback.md`
and the tailor skill tell a candidate to do -- add facts, confirm them at a
terminal, build, apply, seal, verify, prepare for the interview, read the
funnel -- because the defects this catches live between those steps: a hash
recorded by one command and checked by another, a file one writes and another
reads.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from core.apply import Target, apply, load_applications, record_outcome
from core.buildcmd import build
from core.factscmd import add_from_file, confirm
from core.finalize import finalize, verify, verify_ledger
from core.funnel import funnel
from core.interview import prepare
from core.paths import ensure_home
from core.profile.store import load_facts
from core.resume.io import dump_doc
from tests.test_build import FACTS, sample_doc

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
JOB = "acme-ml"


class _Tty(io.TextIOBase):
    """A candidate at their terminal, answering "yes" to every fact."""

    def readline(self, size: int = -1, /) -> str:
        return "y\n"

    def write(self, s: str, /) -> int:
        return len(s)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


@pytest.mark.slow
def test_a_candidate_can_go_from_facts_to_a_sealed_application(home: Path, tmp_path: Path) -> None:
    # 1. Facts arrive unconfirmed, and are unusable until the candidate says so.
    facts_file = tmp_path / "facts.yaml"
    bodies = [
        f.model_dump(mode="json")
        | {"provenance": {"source": "import:x.pdf", "trust": "import_unconfirmed"}}
        for f in FACTS.values()
    ]
    facts_file.write_text(yaml.safe_dump(bodies, sort_keys=False), encoding="utf-8")
    added, rejected = add_from_file(facts_file)
    assert rejected == []
    assert sorted(added) == sorted(FACTS)
    assert not any(f.selectable() for f in load_facts().values())
    assert sorted(confirm([], open_tty=_Tty)) == sorted(FACTS)

    # 2. The posting, then a build that must pass its gates.
    (home / "inbox" / f"{JOB}.md").write_text(
        "# Machine Learning Intern - Acme Robotics\n\nWe ship Python.\n", encoding="utf-8"
    )
    doc_path = tmp_path / "resume.yaml"
    doc_path.write_text(dump_doc(sample_doc()), encoding="utf-8")
    result = build(doc_path, JOB)
    assert result.report.verdict == "pass", result.report.outcomes

    # 3. The packet, which is what the employer receives.
    target = Target(
        job_id=JOB, company="Acme Robotics", title="Machine Learning Intern", source="greenhouse"
    )
    packet = apply(target, now=NOW)
    assert {p.name for p in packet.iterdir()} == {"resume.pdf", "resume.txt", "fields.json"}

    # 4. Sealing, and the proof that nothing changed afterwards.
    final = finalize(JOB, now=NOW)
    assert verify(JOB) == []
    assert verify_ledger() == []
    sealed = final / "resume.pdf"
    sealed.chmod(0o644)
    sealed.write_bytes(b"%PDF tampered")
    assert verify(JOB) == ["resume.pdf does not match its sealed hash"]

    # 5. The interview pack describes what was sent, from the recorded hashes.
    record_outcome(JOB, "interview", now=NOW + timedelta(days=6))
    prep = prepare(JOB).read_text(encoding="utf-8")
    assert "# Interview prep: Machine Learning Intern at Acme Robotics" in prep
    assert "We ship Python." in prep
    assert "Cut the review queue to 300 items" in prep

    # 6. The funnel counts that reply against the source applied through.
    rows = {r.source: r for r in funnel(load_applications(), now=NOW + timedelta(days=7)).rows}
    assert (rows["greenhouse"].applied, rows["greenhouse"].replied) == (1, 1)
    assert rows["greenhouse"].median_days_to_reply == 6.0
