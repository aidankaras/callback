from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.apply import Target, apply
from core.interview import InterviewError, prepare
from core.paths import ensure_home
from core.profile.models import Fact, FactKind, Provenance, Trust
from core.profile.store import facts_sha256, save_facts

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
CONFIRMED = Provenance(source="x", trust=Trust.USER_CONFIRMED)
RESUME = """identity: {name: Dana Vex, email: dana@example.com}
sections:
- title: Experience
  entries:
  - heading: Northwind Analytics
    sources: [role]
    bullets:
    - {text: Cut the review queue to 300 items, sources: [queue]}
"""


@pytest.fixture
def applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    save_facts(
        {
            "queue": Fact(
                id="queue",
                kind=FactKind.EXPERIENCE_DETAIL,
                text="Rebuilt triage so the review queue fell from 1,200 to 300 items in six weeks",
                provenance=CONFIRMED,
            )
        }
    )
    (home / "inbox" / "acme-ml.md").write_text(
        "# ML Intern - Acme\n\nWe want someone who ships.\n", encoding="utf-8"
    )
    draft = home / "outputs" / "acme-ml" / "draft"
    draft.mkdir(parents=True)
    pdf = b"%PDF-1.7 sent resume"
    (draft / "resume.pdf").write_bytes(pdf)
    (draft / "resume.yaml").write_text(RESUME, encoding="utf-8")
    report = {
        "verdict": "pass",
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "doc_sha256": hashlib.sha256(RESUME.encode()).hexdigest(),
        "facts_sha256": facts_sha256(),
    }
    (draft / "report.json").write_text(json.dumps(report), encoding="utf-8")
    apply(Target(job_id="acme-ml", company="Acme", title="ML Intern", source="greenhouse"), now=NOW)
    return home


def test_the_pack_holds_the_posting_what_was_sent_and_the_facts_behind_each_claim(
    applied: Path,
) -> None:
    text = prepare("acme-ml").read_text(encoding="utf-8")
    assert "# Interview prep: ML Intern at Acme" in text
    assert "We want someone who ships." in text
    assert "Cut the review queue to 300 items" in text
    # The story behind the bullet is the fact's own fuller text.
    assert "fell from 1,200 to 300 items in six weeks" in text


def test_a_packet_changed_since_applying_is_refused(applied: Path) -> None:
    """The pack must describe what the interviewer actually has. Mutation:
    skip the hash check -- this test fails."""
    (applied / "outputs" / "acme-ml" / "packet" / "resume.pdf").write_bytes(b"%PDF edited")
    with pytest.raises(InterviewError, match="changed since"):
        prepare("acme-ml")


def test_a_draft_that_no_longer_matches_what_was_sent_is_not_used_for_stories(
    applied: Path,
) -> None:
    """The bullets come from resume.yaml, which is only the source of the sent
    PDF while the build report still ties them together."""
    doc = applied / "outputs" / "acme-ml" / "draft" / "resume.yaml"
    doc.write_text(RESUME.replace("300 items", "250 items"), encoding="utf-8")
    with pytest.raises(InterviewError, match=r"resume\.yaml"):
        prepare("acme-ml")


def test_a_job_never_applied_to_has_no_pack(applied: Path) -> None:
    with pytest.raises(InterviewError, match="no application"):
        prepare("never")


@pytest.mark.parametrize("name", ["resume.txt", "answers.md"])
def test_a_quoted_packet_file_must_match_what_was_recorded(applied: Path, name: str) -> None:
    """The pack quotes resume.txt and answers.md under "What you sent", so each
    must still be the file the application recorded. Mutation: skip the loop
    over recorded hashes -- the edited resume.txt is quoted; an answers.md the
    application never sent is quoted."""
    path = applied / "outputs" / "acme-ml" / "packet" / name
    assert (name == "resume.txt") == path.exists()  # premise: no answers were sent
    path.write_text("PhD, MIT. Staff Engineer at Google\n", encoding="utf-8")
    with pytest.raises(InterviewError, match=name.replace(".", r"\.")):
        prepare("acme-ml")


def test_a_story_is_told_only_from_a_fact_still_confirmed(applied: Path) -> None:
    """Mutation: drop the selectable() check -- the unconfirmed text is printed."""
    save_facts(
        {
            "queue": Fact(
                id="queue",
                kind=FactKind.EXPERIENCE_DETAIL,
                text="UNCONFIRMED: also won a Turing Award",
                provenance=Provenance(source="import:x.pdf", trust=Trust.IMPORT_UNCONFIRMED),
            )
        }
    )
    text = prepare("acme-ml").read_text(encoding="utf-8")
    assert "Turing" not in text
    assert "no longer confirmed" in text


def test_a_story_from_interview_practice_is_a_draft_fact_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skill drafts a practice answer as a fact under this provenance; it
    backs nothing until the candidate confirms it, and then backs the bullet."""
    import io

    from core.checks.grounding import FindingKind, Verdict, check_bullet
    from core.factscmd import add_from_file, confirm
    from core.profile.store import load_facts

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    drafted = tmp_path / "story.yaml"
    drafted.write_text(
        "- id: outage\n"
        "  kind: experience_detail\n"
        "  text: Led the rollback during the March ingestion outage\n"
        "  provenance: {source: 'assistant:interview:acme-ml', trust: import_unconfirmed}\n",
        encoding="utf-8",
    )
    assert add_from_file(drafted) == (["outage"], [])
    fact = load_facts()["outage"]
    assert not fact.selectable()
    verdict, findings = check_bullet("Led the rollback during the ingestion outage", [fact])
    assert verdict is Verdict.FAIL
    assert FindingKind.UNSELECTABLE_FACT in {f.kind for f in findings}

    class _Yes(io.TextIOBase):
        def readline(self, size: int = -1, /) -> str:
            return "y\n"

        def write(self, s: str, /) -> int:
            return len(s)

    assert confirm(["outage"], open_tty=_Yes) == ["outage"]
    verdict, _ = check_bullet(
        "Led the rollback during the ingestion outage", [load_facts()["outage"]]
    )
    assert verdict is not Verdict.FAIL


def test_a_fact_retired_since_sending_is_still_the_story_behind_the_claim(applied: Path) -> None:
    """The document went out resting on this fact. Retiring it afterwards takes
    it out of future documents; it does not unsay what was sent, and the
    interviewer will ask about the claim as written."""
    from datetime import UTC, datetime

    from core.profile.store import load_facts

    facts = load_facts()
    assert facts["queue"].selectable()
    retired_on = datetime(2026, 3, 1, tzinfo=UTC).date()
    save_facts({**facts, "queue": facts["queue"].model_copy(update={"retired_on": retired_on})})

    text = prepare("acme-ml").read_text(encoding="utf-8")

    assert f"  - {facts['queue'].text} (retired 2026-03-01, after this was sent)" in text
    assert "no longer confirmed" not in text


def test_a_retired_fact_nobody_confirmed_tells_no_story(applied: Path) -> None:
    from datetime import UTC, datetime

    save_facts(
        {
            "queue": Fact(
                id="queue",
                kind=FactKind.EXPERIENCE_DETAIL,
                text="UNCONFIRMED: also won a Turing Award",
                provenance=Provenance(source="import:x.pdf", trust=Trust.IMPORT_UNCONFIRMED),
                retired_on=datetime(2026, 3, 1, tzinfo=UTC).date(),
            )
        }
    )

    text = prepare("acme-ml").read_text(encoding="utf-8")

    assert "Turing" not in text
    assert "no longer confirmed" in text
