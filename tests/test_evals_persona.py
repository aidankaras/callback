"""The eval persona must exist in a fresh checkout, not only in the working tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONA = "evals/persona/home"


def test_every_persona_file_on_disk_is_tracked_by_git() -> None:
    """`seed.sh` copies the whole directory; an ignore rule once kept its posting out of git."""
    on_disk = {str(p.relative_to(ROOT)) for p in (ROOT / PERSONA).rglob("*") if p.is_file()}
    tracked = set(
        subprocess.run(
            ["git", "ls-files", PERSONA], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    assert f"{PERSONA}/inbox/acme-ml.md" in on_disk
    assert on_disk == tracked, sorted(on_disk ^ tracked)
